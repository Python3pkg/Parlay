"""
Protocols for Elite Engineering devices
"""
from parlay.protocols.protocol import BaseProtocol
from parlay.server.broker import Broker
from twisted.internet import defer
from twisted.internet.serialport import SerialPort
from twisted.protocols.basic import LineReceiver
from parlay.endpoints.parlay_standard import ParlayCommandEndpoint, parlay_command, BadStatusError, MSG_TYPES
from serial_terminal import SerialTerminal
from math import radians, degrees, sqrt, atan2, pi, acos, sin, cos, asin
from parlay.protocols.utils import delay, timeout
import time

L0 = 8.5  # inches, upper arm length
L1 = 7.0  # inches, forearm length

THETA1_INIT = 0.0   # degrees
THETA2_INIT = 110.0  # degrees up from horizontal, corresponding to zero shoulder motor coordinate
THETA3_INIT = 90.0  # degrees down from straight, corresponding to zero elbow motor coordinate
THETA4_INIT = 17.0   # degrees up from straight, corresponding to zero wrist motor coordinate
THETA5_INIT = -90.0

BASE_ROT_ANGLE_TO_MOTOR = 90.0/7500.0 # degrees per motor step count
SHOULDER_ANGLE_TO_MOTOR = 90.0/1200.0  # degrees per motor step count
ELBOW_ANGLE_TO_MOTOR = 45.0/1000.0

WRIST_PITCH_TO_MOTOR = 90.0/2200.0
WRIST_ROLL_TO_MOTOR = 90.0/1000.0

MAX_RADIUS = L0 + L1 - 1  # max radius to keep the arm within.
                          #  If instructed to go outside this radius, will instead touch the radius

class EliteArmProtocol(SerialTerminal):

    @classmethod
    def open(cls, broker, port="/dev/tty.usbserial-FTAJOUB2"):
        if isinstance(port, list):
            port = port[0]

        EliteArmProtocol.delimiter = '\r'  # delimiter
        p = EliteArmProtocol(port)
        SerialPort(p, port, broker._reactor, baudrate=115200)

        return p

    @classmethod
    def get_open_params_defaults(cls):
        parent_args = SerialTerminal.get_open_params_defaults()
        #just copy over the port list, we already know the baudrate and delimiter
        args = {}
        args["port"] = parent_args["port"]
        return args

    def __init__(self, port):
        BaseProtocol.__init__(self)
        self._parlay_name = port
        self.endpoints = [EliteArmEndpoint(self._parlay_name, self._parlay_name, self)]


class EliteArmEndpoint(ParlayCommandEndpoint):
    """
    Endpoint for an Elite Arm
    """

    def __init__(self, endpoint_id, name, protocol):
        ParlayCommandEndpoint.__init__(self, endpoint_id, name)
        self._protocol = protocol
        self._inited = False
        self._in_move = False

    @defer.inlineCallbacks
    def wait_for_ack(self):

        while True:
            next_resp = yield timeout(self.wait_for_next_sent_msg(), .5)
            if next_resp["TOPICS"].get("MSG_TYPE", "") == MSG_TYPES.EVENT:
                resp = next_resp["CONTENTS"]["DATA"]
                # ack or anything endwith with OK is OK
                if resp != "ACK" and not resp.endswith("OK"):
                    raise BadStatusError(next_resp)
                else:
                    defer.returnValue(next_resp)


    @parlay_command
    def send_raw_data(self, data):
        self._protocol.sendLine(str(data))


    @parlay_command
    def home(self, motor_num):
        self.send_raw_data("HA"+str(motor_num))

    @parlay_command
    def init_motors(self):
        self.send_raw_data("EAL")

    @defer.inlineCallbacks
    @parlay_command
    def set_move_rate(self, rate_ms):
        for i in range(6):
            self.send_raw_data("SMT"+str(i+1)+" " + rate_ms)
            yield self.wait_for_ack()


    @defer.inlineCallbacks
    @parlay_command
    def home_all(self):
        for x in range(1, 7):
            self.home(x)
            yield self.wait_for_ack()

    @parlay_command
    def shutdown(self):
        self.send_raw_data("SHUTDOWN")

    @defer.inlineCallbacks
    @parlay_command
    def get_positions(self):
        self.send_raw_data("REFB")

        next_msg = yield self.wait_for_next_sent_msg()
        val_str = next_msg["CONTENTS"].get("DATA", "")
        # skip every other one (that's rate info)
        vals = [int(x) for x in val_str.split(" ") if int(x) % 2 == 0]
        defer.returnValue(vals)

    @defer.inlineCallbacks
    @parlay_command
    def move_all_motors(self, motor1, motor2, motor3, motor4, motor5, motor6):

        assert -7500 < int(motor1) < 7500
        assert -1200 < int(motor2) < 1200
        assert -1700 < int(motor3) < 2700
        #even out motor 4
        motor4 = max(min(1200, motor4), -2000)
        assert -2000 <= int(motor4) <= 1200
        assert -4200 < int(motor5) < 4200
        if self._in_move:
            return  # we're already moving!! #TODO: Throw an exception
        try:
            self._in_move = True
            #self.send_raw_data("SPCA " + " ".join([str(int(x)) for x in [motor1, motor2, motor3, motor4, motor5, motor6]]))
            m = [motor1, motor2, motor3, motor4, motor5, motor6]
            for i in range(len(m)):
                self.send_raw_data("SPC"+str(i+1)+" "+str(int(m[i])))
                yield self.wait_for_ack()
        finally:
            self._in_move = False


    @defer.inlineCallbacks
    def move_all_motors_and_wait(self, motor1, motor2, motor3, motor4, motor5, motor6):
        self.move_all_motors(motor1, motor2, motor3, motor4, motor5, motor6)
        yield self.wait_for_ack()
        stationary = False
        old_pos = [None, None, None, None, None, None]
        while not stationary:
            yield delay(0.05)
            self.send_raw_data("REFB")
            next_resp = yield self.wait_for_next_sent_msg()
            pos = [int(x) for x in next_resp["CONTENTS"]["DATA"].split(" ")[::2] ]
            # are the all the same (e.g. haven't moved? since last reading?)
            stationary = all([pos[i] == old_pos[i] for i in range(6)])
            old_pos = pos

        print "Done Moving"

    @parlay_command
    def move_hand(self, x, y, z, wrist_pitch, wrist_roll):
        """
        Kinematic move
        """
        try:
            print "Moving hands"
            x,y,z = Kinematics.scale_to_max_radius(x,y,z)
            thetas = Kinematics.xyz_to_joint_angles(float(x), float(y), float(z))
            m1 = Kinematics.base_angle_to_motor(thetas[0])
            m2 = Kinematics.shoulder_angle_to_motor(thetas[1])
            m3 = Kinematics.elbow_angle_to_motor(thetas[2])
            m4 = Kinematics.wrist_pitch_to_motor(Kinematics.pitch_to_wrist_angle(thetas[1], thetas[2], float(wrist_pitch)))
            m5 = Kinematics.wrist_roll_to_motor(Kinematics.roll_to_wrist_roll_angle(float(wrist_roll)))

            print (m1, m2, m3)
            self.move_all_motors(m1, m2, m3, m4, m5, 0)
        except Exception as e:
            print str(e)



#Math Functions
class Kinematics:

    @staticmethod
    def scale_to_max_radius(x, y, z):
        r = sqrt(x*x + y*y + z*z)
        if r > MAX_RADIUS:
            x = x*MAX_RADIUS/r
            y = y*MAX_RADIUS/r
            z = z*MAX_RADIUS/r

        return x,y,z

    @staticmethod
    def _xyz_to_cylindrical(x, y, z):
        """
        Converts cartesian coordinates x, y, z to cylindrical coordinates r, h, phi
        :param x: radially out at zero phi angle
        :param y: radially out at 90 deg phi angle
        :param z: strictly equal to height
        :return: height, radius, phi in degrees
        """
        r = sqrt(x**2 + y**2)
        h = z
        phi = degrees(atan2(y, x))

        return r, h, phi

    @staticmethod
    def _cylindrical_to_xyz(r, h, phi):
        x = r * cos(radians(phi))
        y = r * sin(radians(phi))
        z = h

        return x, y, z

    @staticmethod
    def xyz_to_joint_angles(x, y, z):
        """
        Inverse kinematics, convert desired hand position to required joint angles.

        Input coordinates are in inches.
        Output joint angles are in degrees.

        theta1 = Base rotation angle
        theta2 = Shoulder up angle
        theta3 = Elbow bend angle

        :param x: straight out along forward axis of arm.  Positive x moves away from base
        :param y: perpindicular to x, with no increase in height.  Positive y moves right from base perspective.
        :param z: height above base. Positive is up.
        :return: (theta1, theta2, theta3)
        """
        r, h, phi = Kinematics._xyz_to_cylindrical(x, y, z)

        theta3_rad = pi - acos((L0**2 + L1**2 - r**2 - h**2) / (2 * L0 * L1))

        rho = atan2(L1 * sin(theta3_rad), L0 + L1 * cos(theta3_rad))
        k = sqrt(L0**2 + L1**2 + 2 * L0 * L1 * cos(-theta3_rad))

        theta2_rad = rho + acos(r / k)
        theta1_rad = atan2(y, x)

        theta1 = degrees(theta1_rad) - THETA1_INIT
        theta2 = degrees(theta2_rad) - THETA2_INIT
        theta3 = degrees(theta3_rad) - THETA3_INIT

        return theta1, theta2, theta3

    @staticmethod
    def joint_angles_to_xyz(theta1, theta2, theta3):
        """
        Converts joint angles of arm into x, y, z cartesian coordinates
        :param theta1: arm base rotation angle in degrees
        :param theta2: arm shoulder up angle in degrees
        :param theta3: arm elbow bend angle in degrees
        :return: x, y, z in inches
        """
        theta2_rad = radians(theta2 + THETA2_INIT)
        theta3_rad = radians(theta3 + THETA3_INIT)

        r = L0 * cos(theta2_rad) + L1 * cos(theta2_rad - theta3_rad)
        h = L0 * sin(theta2_rad) + L1 * sin(theta2_rad - theta3_rad)
        phi = theta1 + THETA1_INIT

        x, y, z = Kinematics._cylindrical_to_xyz(r, h, phi)
        return x, y, z

    @staticmethod
    def pitch_to_wrist_angle(theta2, theta3, pitch):
        return pitch - theta2 + theta3 - THETA4_INIT

    @staticmethod
    def roll_to_wrist_roll_angle(roll):
        return roll - THETA5_INIT

    @staticmethod
    def shoulder_angle_to_motor(angle):
        return angle / SHOULDER_ANGLE_TO_MOTOR

    @staticmethod
    def elbow_angle_to_motor(angle):
        return angle / ELBOW_ANGLE_TO_MOTOR

    @staticmethod
    def base_angle_to_motor(angle):
        return angle / BASE_ROT_ANGLE_TO_MOTOR

    @staticmethod
    def wrist_pitch_to_motor(angle):
        return angle / WRIST_PITCH_TO_MOTOR

    @staticmethod
    def wrist_roll_to_motor(angle):
        return angle / WRIST_ROLL_TO_MOTOR




