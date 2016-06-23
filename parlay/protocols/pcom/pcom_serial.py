from twisted.internet.serialport import SerialPort
from twisted.protocols.basic import LineReceiver
from twisted.internet import defer

# Testing
from parlay import parlay_command, start
# Testing
from parlay.protocols.serial_line import ASCIILineProtocol, LineItem

from parlay.protocols.base_protocol import BaseProtocol
from parlay.protocols.utils import message_id_generator, timeout, MessageQueue, TimeoutError, delay

from serial.tools import list_ports
import service_message
from serial_encoding import *

import struct


class PCOM_Serial(BaseProtocol, LineReceiver):

    # Command code is 0x00 for discovery

    NUM_RETRIES = 3
    DISCOVERY_SERVICE_CODE = 0xfefe


    # The minimum event ID. Some event IDs may need to be reserved
    # in the future.
    MIN_EVENT_ID = 0
    NUM_EVENT_ID_BITS = 16

    # Number of bits we have for sequence number
    SEQ_BITS = 4


    @classmethod
    def open(cls, broker, port, baudrate):
        '''

        :param cls: The class object (supplied by system)
        :param broker: current broker instance (supplied by system)
        :param port: the serial port device to use. On linux, something like/dev/ttyUSB0
        :return: returns the instantiated protocol object

        '''

        # Make sure port is not a list
        port = port[0] if isinstance(port, list) else port

        protocol = PCOM_Serial(broker)
        print "Serial Port constructed with port " + str(port)
        SerialPort(protocol, port, broker._reactor, baudrate=57600)

        return protocol

    @classmethod
    def get_open_params_defaults(cls):

        '''
        Returns a list of parameters defaults. These will be displayed in the UI.
        :return:
        '''

        default_args = BaseProtocol.get_open_params_defaults()

        potential_serials =  [port_list[0] for port_list in list_ports.comports()]
        default_args['port'] = potential_serials
        default_args['baudrate'] = [300, 1200, 2400, 4800, 9600, 14400, 19200, 28800, 38400, 57600, 115200, 230400]

        return default_args

    def close(self):
        '''
        Simply close the connectection
        :return:
        '''
        self.transport.loseConnection()
        return defer.succeed(None)

    def __init__(self, broker):
        """
        :param broker: The Broker singleton that will route messages
        :param system_ids: A list of system_ids that are connected, or None to do a discovery

        """

        # A list of items that we will need to discover for
        self.items = []

        BaseProtocol.__init__(self)

        # Set the LineReceiver to line mode. This causes lineReceived to be called
        # when data is sent to the serial port. We will get a line whenever the END_BYTE
        # appears in the buffer
        self.setLineMode()
        self.delimiter = END_BYTE_STR

        # The buffer that we will be storing the data that arrives via the serial connection
        self._binary_buffer = bytearray()

        self.broker = broker

        # Event IDs are 16-bit (2 byte) numbers so we need a radix
        # of 65535 or 0xFFFF in hex
        # NOTE: The number of bits in an event ID is subject to change,
        # the constant NUM_EVENT_ID_BITS can easily be changed to accommodate this.
        self._event_id_generator = message_id_generator((2**self.NUM_EVENT_ID_BITS))

        # From parlay.utils, calls _send_message_down_transport() whenever
        # a new message is added to the MessageQueue object
        self._event_queue = MessageQueue(self._send_message_down_transport)

        self._attached_system_d = None

        # Dictionary that maps ID # to Deferred object
        self._discovery_msg_ids = {}

        # Sequence number is a nibble as of now, so the domain should be
        # 0 <= seq number <= 15
        # which means the radix will be 16, but to be safe I'll do
        # 2^SEQ_BITS where SEQ_BITS is a member constant that can easily be changed
        self._seq_num = message_id_generator((2**self.SEQ_BITS))


        # ACKs should be deferred objects because you want to catch them on the way
        # back via asynchronous communication.
        self._ack_deferred = defer.Deferred()

    @defer.inlineCallbacks
    def _send_message_down_transport(self, message):
        """
        This is the callback function given to the MessageQueue object that is called
        whenever a new message is added to the queue.

        This function does the actual writing to the serial port.

        :type message dict
        """
        # Turn it into a service message that we can understand.
        s = service_message.ServiceMessage.from_dict_msg(message)

        # Serialize the message and prepare for protocol wrapping.
        packet = encode_service_message(s)
        need_ack = False

        # Get the next sequence number and then wrap the protocol with
        # the desired low level byte wrapping and send down serial line
        sequence_num = self._seq_num.next()
        packet = str(wrap_packet(packet, sequence_num, need_ack))

        # Write to serial line! Good luck packet.
        self.transport.write(packet)

        # Testing
        print "This was sent down the serial line:"
        print "--->", [hex(ord(x)) for x in packet]

        num_retries_left = self.NUM_RETRIES
        while need_ack and num_retries_left > 0:
            try:
                ack_sequence_num = yield timeout(self._ack_deferred, .5)
                if ack_sequence_num == sequence_num:
                    # print "ACK"
                    need_ack = False  # we got it, no need to wait
                else:
                    print "Wrong Seq Num? ", ack_sequence_num, "!=", sequence_num
            except TimeoutError:
                # retry
                print "RETRY"
                self._ack_deferred = defer.Deferred()  # set up a new one
                self.transport.write(packet)  # try again
                num_retries_left -= 1

        defer.returnValue(message)

    def _discovery_listener(self, msg):
        """
        We need did this function to fire the deferred objects based on the msg we receive.
        If the message ID matches an ID in the dictionary, fire the deferred.

        :type msg service_message.ServiceMessage

        """
        # Return if there aren't any IDs left
        if len(self._discovery_msg_ids) == 0:
            return

        if msg.msg_type == service_message.MsgType.COMMAND_RESPONSE and msg.msg_id in self._discovery_msg_ids:
            # If the message was a response and matched an ID in the dictionary, remove it and fire the
            # corresponding Deferred object.
            self._discovery_msg_ids.pop(msg.msg_id).callback(msg)

    def send_command(self, to, tx_type, command_id=0, msg_status="INFO", response_req=True):
        """

        Send a command and return a deferred that will succeed on a response and with the response

        """

        # Increment the event ID
        event_id = self._event_id_generator.next()

        # Construct the message based on the parameters

        # For now "FROM:" will always be the discovery code,
        # this needs to change in the future.
        # Build the TOPICS portion
        topics = {

                "MSG_ID": event_id,
                "TX_TYPE": tx_type,
                "MSG_TYPE": "COMMAND",
                "RESPONSE_REQ": response_req,
                "MSG_STATUS": msg_status,
                # NOTE: Change this to handle sending and receiving across subsystems.
                "FROM": self.DISCOVERY_SERVICE_CODE,
                "TO": to

        }

        # Build the CONTENTS portion
        contents = {

                "COMMAND": command_id
        }

        # If we need to wait the result should be a deferred object.
        if response_req:
            result = defer.Deferred()
            # Add the correct mapping to the dictionary
            self._discovery_msg_ids[event_id] = result

        # Message will be added to event queue and
        # sent down serial line (via callback function _send_down_transport())
        self._event_queue.add({"TOPICS": topics, "CONTENTS": contents})

        # Return the Deferred object if we need to
        return result

    def connectionMade(self):
        '''
        The initializer for the protocol. This function is called when a connection to the server
        (broker in our case) has been established. Keep this function LIGHT, it should not take a long time
        to fetch the subsystem and item IDs. The user typically shouldn't notice.

        I wrote a function _get_attached_systems() that is called here and also when a discovery takes place.
        :return: None
        '''
        self._get_attached_systems()
        return

    @defer.inlineCallbacks
    def _get_attached_systems(self):
        '''
        A generator that returns all attached system IDs
        NOTE: This is a subroutine of the discovery process. This method should be lightweight because
        it also going to be called upon connection establishment. We don't want the user waiting around forever
        when their device is connected.

        :return: All attached system IDs

        '''

        # If we have stored systems, return them first
        while self._attached_system_d is not None:
            yield self._attached_system_d

        # Create a new deferred object because this is an asynchronous operation.
        self._attached_system_d = defer.Deferred()

        # The first part of the discovery protocol
        # is to fetch all subsystems. The reactor inside of
        # the embedded core should return with each subsystem as a
        # ID, Name pair (eg. (0, "IO_Control_board"))
        subsystem_ids = yield self._get_subsystems()

        # Convert subsystem IDs to ints so that we can send them
        # back down the serial line to retrieve their attached items
        subsystem_ids = [ord(x[0]) for x in subsystem_ids]


        print "SUBSYSTEMS FOUND: ", subsystem_ids

        # For each subsystem ID, fetch the items attached to it
        # for subsystem in subsystem_ids:
        #    response = yield self.send_command((subsystem << 8), "DIRECT")



        # TODO: Explain this in comments
        d = self._attached_system_d
        self._attached_system_d = None
        d.callback(None)

    @defer.inlineCallbacks
    def _get_subsystems(self):
        '''
        Sends a broadcast message. A broadcast message goes to the reactor and expects a list of
        subsystems in return.
        :return:
        '''

        # NOTE: Multiple messages may be sent back for the subsystem
        sub_systems = []
        response = yield self._send_broadcast_message()
        print "RESPONSE ", response
        sub_systems += response.data
        defer.returnValue(sub_systems)

    @defer.inlineCallbacks
    def get_discovery(self):
        """

        Hitting the "discovery" button on the UI triggers this generator.

        Run a discovery for everything connected to this protocol and return a list of of all connected:
        items, messages, and endpoint types

        """
        print "----------------------------"
        print "Discovery function started!"
        print "----------------------------"

        # If there is a deferred system, yield that first
        if self._attached_system_d is not None:
            yield self._attached_system_d

        # Initialize a discovered set. We don't want duplicates.
        already_discovered = set()

        self._send_broadcast_message()

    def _send_broadcast_message(self):
        # Testing
        print "Sending broadcast message..."


        # The item ID of the reactor is 0, which is where we want our broadcast message to go to.
        device_id = 0

        destination_id = device_id

        # The subsystem ID for the broadcast message is 0x80
        # The high byte of the destination ID is the subsystem ID, so we should insert
        # 0x80 into the high byte.
        destination_id += 0x8000

        # The response code, event type, event attributes, and format string are all zero for a
        # broadcast message
        return self.send_command(to=destination_id, command_id=0, tx_type="BROADCAST")


    def _send_ack(self, sequence_num):

        '''
        Sends an ACK message down the serial line.

        :param sequence_num:
        :return:
        '''



    def _encode_event(self, event_id, from_id, to_id, response_code, event_type, event_attrs, format_string, data=None):
        '''
        :param event_id: Identifier that is unique to the event.
        :param from_id: AKA source ID. This will be the ID of the device in which the event comes from.
        :param to_id: AKA destination ID. This will be the ID of the device that receives this message.
        :param response_code: Depending on the type of event, this could be a command ID, property ID, or status code.
        :param event_type: The type of message. Bits 0-3 are the subtype and bits 4-7 are the category.
        :param event_attrs: Attributes of the event.
                            Bit 0 is the priority of the event. A 0 represents normal priority
                            and a 1 represents a high priority. High priority events are placed in the front of the queue
                            (eg. an interrupt).
                            Bit 1 is the response expected. It applies to orders only and is a way to send a command
                            or property set without getting a response. (0 = response expected, 1 = no response expected).

        :param format_string: Describes the structure of the data using a character for each type.
                                -------------------------------------------------
                                | Type              | Character     | # bytes   |
                                |-------------------|---------------|-----------|
                                | unsigned byte     |    B          |    1      |
                                |-------------------|---------------|-----------|
                                | signed byte       |    b          |    1      |
                                |-------------------|---------------|-----------|
                                | padding           |    x          |    1      |
                                |-------------------|---------------|-----------|
                                | character         |    c          |    1      |
                                |-------------------|---------------|-----------|
                                | unsigned short    |    H          |    2      |
                                |-------------------|---------------|-----------|
                                | signed short      |    h          |    2      |
                                |-------------------|---------------|-----------|
                                | unsigned int      |    I          |    4      |
                                |-------------------|---------------|-----------|
                                | signed int        |    i          |    4      |
                                |-------------------|---------------|-----------|
                                | unsigned long     |    Q          |    8      |
                                |-------------------|---------------|-----------|
                                | signed long       |    q          |    8      |
                                |-------------------|---------------|-----------|
                                | float             |    f          |    4      |
                                |-------------------|---------------|-----------|
                                | double            |    d          |    8      |
                                |-------------------|---------------|-----------|
                                | string            |    s          |    ?      |
                                |-------------------|---------------|-----------|

        :param data:
        :return: Returns a string of bytes
        '''


        # Build the sequence of bytes that is to be sent serially to the Embedded Core Reactor
        print "Building byte sequence..."

        buffer = ''
        buffer += struct.pack("<H", event_id)
        buffer += struct.pack("<H", from_id)
        buffer += struct.pack("<H", to_id)
        buffer += struct.pack("<H", response_code)
        buffer += struct.pack("<B", event_type)
        buffer += struct.pack("<B", event_attrs)
        buffer += struct.pack("<s", format_string)

        # Don't send data if there isn't any to send.
        if data is not None:
            buffer += struct.pack("<s", data)

        print "Buffer has been built..."

        return buffer

    def _p_wrap(self, stream):
        '''
        Do the promenade wrap! The promenade protocol looks like:

        START BYTE <byte sequence> END BYTE

        Where START BYTE and END BYTE are 0x02 and 0x02 (for now at least).

        Since there is a possibility of 0x02 and 0x03 appearing in the data stream we must added 0x10 to all
        0x10, 0x02, 0x03 bytes and 0x10 should be inserted before each "problem" byte.

        For example

        stream = 0x03 0x04 0x05 0x06 0x07
        _p_wrap(stream) = 0x02 0x10 0x13 0x04 0x05 0x06 0x07 0x03

        :param stream: A raw stream of bytes
        :return: A bytearray that has been run through the Promenade protocol


        '''

        msg = bytearray()
        msg.append(START_BYTE) # START
        for b in stream:
            if b in [START_BYTE, ESCAPE_BYTE, END_BYTE]:
                msg.append(ESCAPE_BYTE)
                msg.append(b + ESCAPE_BYTE)
            else:
                msg.append(b)
        msg.append(END_BYTE)
        return msg

    @defer.inlineCallbacks
    def _fetch_system_discovery(self, device_id, already_discovered):
        """

        A "private" function to discover the commands, item IDs, etc.. given a system ID.
        In other words, the PCOM discovery protocol will be run on system_id

        """
        destination_id = device_id

        # The subsystem ID for the broadcast message is 0x80
        # The high byte of the destination ID is the subsystem ID, so we should insert
        # 0x80 into the high byte.
        destination_id += 0x8000


        # Make sure IDs are 0 < ID < 65535 before encoding.


        self._binary_buffer = bytearray(self._encode_event(0, self.DISCOVERY_SERVICE_CODE, device_id, 0, 0, 0, 0))

        print self._binary_buffer

    def rawDataReceived(self, data):
        '''
        This function is called whenever data appears on the serial port
        :param data:
        :return:
        '''

        raise Exception('Using line received!')

    def _on_packet(self, sequence_num, ack_expected, is_ack, is_nak, msg):
        """
        This will get called with every new serial packet.
        The parameters are the expanded tuple gicen from unstuff_packet
        :param sequence_num: the sequence number of the received packet
        :param ack_expected: Is an ack expected to this message?
        :param is_ack : Is this an ack?
        :param is_nak: Is this a nak?
        :param msg: The service message (if it is one and not an ack/nak)
        :type msg : service_message.SSComServiceMessage
        """

        if is_ack:
            # let everyone know we got the ack and make a new deferred
            temp = self._ack_deferred   # temp handle
            self._ack_deferred = defer.Deferred()  # setup a new one for everyone to listen to
            temp.callback(sequence_num)  # callback and invalidate the old deferred
            return
        elif is_nak:
            return  # ignore, the timeout will happen and handle a resend

        # If we need to ack, ACK!
        if ack_expected:
            ack = str(self._p_wrap(ack_nak_message(sequence_num, True)))
            self.transport.write(ack)
            print "---> ACK MESSAGE SENT"
            print [hex(ord(x)) for x in ack]

        # also send it to discovery listener locally
        print 'About to call listener'
        self._discovery_listener(msg)

    def lineReceived(self, line):
        '''
        If this function is called we have received a <line> on the serial port
        that ended in 0x03.


        :param line:
        :return:
        '''

        print "--->Line received was called!"
        print [hex(ord(x)) for x in line]

        #Using byte array so unstuff can use numbers instead of strings
        buf = bytearray()
        start_byte_index = (line.find(START_BYTE_STR) + 1)
        buf += line
        packet_tuple = unstuff_packet(buf[start_byte_index:])
        self._on_packet(*packet_tuple)
        print packet_tuple


'''

        parlay_msg = msg.to_dict_msg()
        self.broker.publish(parlay_msg, self.transport.write)

        # ack the remote device if it expects it
        if ack_expected:
            self.transport.write(str(_escape_packet(ack_nak_message(sequence_num, True))))

        # also send it to discovery listener locally
        self._discovery_listener(msg)
'''





class SerialLEDItem(LineItem):

	def __init__(self, led_index, item_id, name, protocol):
		LineItem.__init__(self, item_id, name, protocol)
		self._led_index = led_index

	@parlay_command()
	def light(self):
		self.send_raw_data(1)
	@parlay_command()
	def dim(self):
		self.send_raw_data(2)

if __name__ == "__main__":
	start()

