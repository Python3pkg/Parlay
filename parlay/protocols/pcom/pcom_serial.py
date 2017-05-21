"""

pcom_serial.py

This protocol enables Parlay to interact with embedded devices. This class handles the passing of messages
between Parlay and embedded devices.


"""

from twisted.internet.serialport import SerialPort
from twisted.protocols.basic import LineReceiver
from twisted.internet import defer
from twisted.internet import reactor

from parlay.items.parlay_standard import ParlayStandardItem, INPUT_TYPES
from parlay.protocols.base_protocol import BaseProtocol
from parlay.protocols.utils import message_id_generator, MessageQueue

from serial.tools import list_ports

import logging

from .serial_encoding import *
from .enums import *

import time
import json

# Constants used in converting format chars to
# Parlay input types
PCOM_SERIAL_NUMBER_INPUT_CHARS = "BbHhIiQqfd"
PCOM_SERIAL_STRING_INPUT_CHARS = "?csx"
PCOM_SERIAL_ARRAY_INPUT_CHARS = '*'

# Store a map of Item IDs -> Command ID -> Command Objects
# Command objects will store the parameter -> format mapping
PCOM_COMMAND_MAP = {}

# Store a map of properties. We must keep track of a
# name -> format mapping in order to serialize data
PCOM_PROPERTY_MAP = {}

# Store a map of command names to IDs
# item ID -> Command name -> ID
PCOM_COMMAND_NAME_MAP = {}

# Map of error codes to their string name equivalent
PCOM_ERROR_CODE_MAP = {}

# Store a map of property names to IDs
# item ID -> Property name -> ID
PCOM_PROPERTY_NAME_MAP = {}

# Store a map of stream names
# item ID -> Stream name -> Stream ID
PCOM_STREAM_NAME_MAP = {}

# Store a map of item ID to item names
# item ID -> item name
PCOM_ITEM_NAME_MAP = {}

logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)

# A namedtuple representing the information of each property.
# This information will be retrieved during discovery.
# name = string representing the name of the property
# format = format describing the type of property.
# Eg. If the property were a floating point value it would be 'f'


class PCOMSerial(BaseProtocol, LineReceiver):

    # Constant number of retries before another message is sent out
    # after not receiving an ACK
    NUM_RETRIES = 3

    # The item ID of the protocol during discovery.
    DISCOVERY_CODE = 0xfefe
    PCOM_RESET_ID = 0xfefe

    # The minimum event ID. Some event IDs may need to be reserved
    # in the future.
    MIN_EVENT_ID = 0

    # The number of bits reserved for the event ID in the serialized
    # event protocol. Eg. if two bytes are reserved this number should be 16.
    NUM_EVENT_ID_BITS = 16

    # Item ID of the embedded reactor
    EMBD_REACTOR_ID = 0
    BROADCAST_SUBSYSTEM_ID = 0x8000
    SUBSYSTEM_SHIFT = 8
    SUBSYSTEM_ID_MASK = 0xFF00
    ITEM_ID_MASK = 0xFF

    # Number of bits we have for sequence number
    SEQ_BITS = 4

    # baud rate of communication over serial line
    BAUD_RATE = 115200

    STM_VCP_STRING = "STM32 Virtual ComPort"
    USB_SERIAL_CONV_STRING = "USB Serial Converter"
    STLINK_STRING = "Link"

    FTDI_VENDOR_ID = "=0403"
    ST_VENDOR_ID = "=0483"
    ST_SNR = "SNR="

    # ACK window size
    WINDOW_SIZE = 8

    ACK_DIFFERENTIAL = 8

    # timeout before resend in secs
    ACK_TIMEOUT = 1

    ERROR_STATUS = DISCOVERY_CODE << 16
    DISCOVERY_TIMEOUT_ID = (DISCOVERY_CODE + 1) << 16
    MESSAGE_TIMEOUT_ERROR_ID = (DISCOVERY_CODE + 2) << 16

    is_port_attached = False

    discovery_file = None

    @classmethod
    def open(cls, adapter, port, discovery_file=None):
        """
        :param cls: The class object
        :param adapter: current adapter instance used to interface with broker
        :param port: the serial port device to use.
        :return: returns the instantiated protocol object
        '"""

        cls.discovery_file = discovery_file
        # Make sure port is not a list
        port = port[0] if isinstance(port, list) else port
        protocol = PCOMSerial(adapter, port)

        cls._open_port(protocol, port, adapter)

        if not cls.is_port_attached:
            raise Exception("Unable to find connected embedded device.")

        return protocol

    @classmethod
    def _open_port(cls, protocol, port, adapter):
        try:
            SerialPort(protocol, port, adapter.reactor, baudrate=cls.BAUD_RATE)
            cls.is_port_attached = True
        except Exception as E:
            logger.error("[PCOM] Unable to open port because of error (exception): {0}".format(E))

    @staticmethod
    def _filter_com_ports(potential_com_ports):

        def _is_valid_port(port_name):
            return PCOMSerial.STM_VCP_STRING in port_name[1] or PCOMSerial.USB_SERIAL_CONV_STRING in port_name[1] or \
                   PCOMSerial.FTDI_VENDOR_ID in port_name[2] or (PCOMSerial.ST_VENDOR_ID in port_name[2] and
                                                                 PCOMSerial.STLINK_STRING not in port_name[1]) or \
                   PCOMSerial.ST_VENDOR_ID in port_name[2] and PCOMSerial.ST_SNR in port_name[2]

        result_list = []
        try:
            for port in potential_com_ports:
                logger.info("PORT:")
                logger.info(port)
                if len(port) > 1:
                    if _is_valid_port(port):
                        result_list.append(port)
        except Exception as e:
            logger.error("[PCOM] Could not filter ports because of exception: {0}".format(e))
            return potential_com_ports

        return result_list if result_list else potential_com_ports

    @classmethod
    def get_open_params_defaults(cls):
        """
        Returns a list of parameters defaults. These will be displayed in the UI.
        :return: default args: the default arguments provided to the user in the UI
        """

        cls.default_args = BaseProtocol.get_open_params_defaults()
        logger.info("[PCOM] Available COM ports: {0}".format(list_ports.comports()))

        filtered_comports = cls._filter_com_ports(list_ports.comports())
        logger.info("[PCOM]: filtered_comports:")
        logger.info(filtered_comports)
        potential_serials = [port_list[0] for port_list in filtered_comports]
        cls.default_args['port'] = potential_serials

        return cls.default_args

    def reset(self):

        self._event_id_generator = message_id_generator((2 ** self.NUM_EVENT_ID_BITS))
        self._seq_num = message_id_generator((2 ** self.SEQ_BITS))

        self._ack_window.reset_window()

        self._ack_table = {seq_num: defer.Deferred() for seq_num in range(2**self.SEQ_BITS)}
        self._ack_window = SlidingACKWindow(self.WINDOW_SIZE, self.NUM_RETRIES)

    def close(self):
        """
        Simply close the connection
        :return:
        """
        self.reset()
        self.transport.loseConnection()
        return defer.succeed(None)

    def __str__(self):
        return "PCOM @ " + str(self._port)

    def __init__(self, adapter, port):
        """
        :param adapter: The adapter that will serve as an interface for interacting with the broker
        """

        self._port = port
        # A list of items that we will need to discover for.
        # The base protocol will use this dictionary to feed items to
        # the UI
        self.items = []
        self._error_codes = []
        self._loaded_from_file = False

        # The list of connected item IDs found in the initial sweep in
        # connectionMade()
        self._item_ids = []
        BaseProtocol.__init__(self)

        # Set the LineReceiver to line mode. This causes lineReceived to be called
        # when data is sent to the serial port. We will get a line whenever the END_BYTE
        # appears in the buffer
        self.setLineMode()
        self.delimiter = END_BYTE_STR

        # The buffer that we will be storing the data that arrives via the serial connection
        self._binary_buffer = bytearray()
        self._subsystem_ids = []

        self.adapter = adapter

        # Event IDs are 16-bit (2 byte) numbers so we need a radix
        # of 65535 or 0xFFFF in hex
        # NOTE: The number of bits in an event ID is subject to change,
        # the constant NUM_EVENT_ID_BITS can easily be changed to accommodate this.
        self._event_id_generator = message_id_generator((2**self.NUM_EVENT_ID_BITS))

        # From parlay.utils, calls _message_queue_handler() whenever
        # a new message is added to the MessageQueue object
        self._message_queue = MessageQueue(self._message_queue_handler)
        self._attached_item_d = None

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

        # Store discovered item IDs so that we do not push duplicates to the
        # item requesting the discovery

        self._discovery_in_progress = False
        self._discovery_deferred = defer.Deferred()

        self._ack_table = {seq_num: defer.Deferred() for seq_num in range(2**self.SEQ_BITS)}

        self._ack_window = SlidingACKWindow(self.WINDOW_SIZE, self.NUM_RETRIES)

    def send_error_message(self, original_message, message_status, description=''):
        """
        Sends a notification error to the destination ID.

        :param original_message: PCOM Message object that holds the IDs of the sender and receiver
        :param message_status: Message status code that translates to an error message.
        :param description: description for the error message to be thrown in CONTENTS
        :return:
        """
        try:
            response_type = MessageCategory.Order_Response << CATEGORY_SHIFT
            error_msg = pcom_message.PCOMMessage(to=original_message.from_, from_=original_message.to,
                                                 msg_status=message_status, msg_id=original_message.msg_id,
                                                 msg_type=response_type, description=description)
            json_msg = error_msg.to_json_msg()
            self.adapter.publish(json_msg)
        except Exception as e:
            logger.error("[PCOM] Unhandled exception in function: to_json_msg(): {0}", e)

    def broadcast_error_message(self, error_id, description, info):
        """
        Broadcasts an error message to the broker. This is mainly used to signal a failed discovery.

        :param error_id: ID of the error message
        :param description: description that will be placed under contents
        :param info: info that will be placed under contents
        :return: None
        """
        json_msg = {
            "TOPICS": {
                "TX_TYPE": "BROADCAST",
                "MSG_TYPE": "EVENT",
                "MSG_STATUS": "ERROR",
                "MSG_ID": next(self._event_id_generator),
                "FROM": self.DISCOVERY_CODE,
            },
            "CONTENTS": {
                "EVENT": error_id,
                "ERROR_CODE": error_id,
                "DESCRIPTION": description,
                "INFO": info
            }}
        self.adapter.publish(json_msg)

    def _message_queue_handler(self, message):
        """
        This is the callback function given to the MessageQueue object that is called
        whenever a new message is added to the queue.

        This function does the actual writing to the serial port.

        :type message dict
        :param message: dictionary message received from Parlay


        """

        # this function should return a fired deferred, so set one up
        d = defer.Deferred()

        try:
            s = pcom_message.PCOMMessage.from_json_msg(message)
        except Exception as e:
            logger.error("[PCOM] Could not translate JSON message to PCOM equivalent because of "
                         "exception: {0}".format(e))
            logger.error("[PCOM] Message that caused PCOM translation error: {0}".format(message))
            d.errback(e)
            return d

        # Serialize the message and prepare for protocol wrapping.
        try:
            packet = encode_pcom_message(s)
        except Exception as e:
            logger.error("[PCOM] Unable to encode pcom message")
            logger.error("[PCOM] Exception: {0}".format(e))
            self.send_error_message(original_message=s, message_status=self.ERROR_STATUS,
                                    description="Unable to encode message: {0} because of "
                                                "exception: {1}".format(message, e))
            d.errback(e)
            return d

        need_ack = True

        # Get the next sequence number and then wrap the protocol with
        # the desired low level byte wrapping and send down serial line
        sequence_num = next(self._seq_num)
        try:
            packet = str(wrap_packet(packet, sequence_num, need_ack))

        except ValueError as v:
            logger.error("[PCOM] Fatal error: created packet with invalid checksum, aborting send.")
            d.errback(v)
            return d
        except Exception as e:
            d.errback(e)
            return d

        d.callback(None)

        disc_msg_deferred = self._discovery_msg_ids[s.msg_id] if self._discovery_in_progress else None
        self._ack_window.add(ACKInfo(sequence_num, 0, packet, self.transport, self.ack_timeout_handler,
                                     disc_msg_deferred))
        return d

    def ack_timeout_handler(self):
        """
        Called when an ACK fails to send
        :return: None
        """
        error_id = self.DISCOVERY_TIMEOUT_ID if self._discovery_in_progress else self.MESSAGE_TIMEOUT_ERROR_ID
        self.broadcast_error_message(error_id, "Message send failed at transport layer.", "Connection failed. Please"
                                               " verify connection with embedded board.")

    def _discovery_listener(self, msg):
        """
        We need did this function to fire the deferred objects based on the msg we receive.
        If the message ID matches an ID in the dictionary, fire the deferred.

        :type msg PCOMMessage

        """
        # Return if there aren't any IDs left
        if len(self._discovery_msg_ids) == 0:
            return

        if msg.category() == MessageCategory.Order_Response and msg.msg_id in self._discovery_msg_ids:
            # If the message was a response and matched an ID in the dictionary, remove it and fire the
            # corresponding Deferred object.
            self._discovery_msg_ids.pop(msg.msg_id).callback(msg)

        return

    """

    The following functions aid in the discovery protocol.
    They may be condensed into fewer functions that require
    more parameters, but I thought abstracting each message
    would making understanding the protocol easier.

    """

    @defer.inlineCallbacks
    def get_property_name(self, to, requested_property_id):
        """
        Sends a message down the serial line requesting the command name of a given command ID,
        used in discovery protocol
        :param to: destination item ID
        :param requested_property_id: property ID that we want to know the name of
        :return: name of the property from Embedded Core
        """
        try:
            response = yield self.send_command(to, command_id=GET_PROPERTY_NAME, params=["property_id"],
                                               data=[requested_property_id])
        except Exception as e:
            logger.error("[PCOM] Unable to find property name for property {0} because of exception: {1}".format(
                requested_property_id, e))
            defer.returnValue(None)

        # The data in the response message will be a list,
        # the property name should be in the 0th position
        # and strip the NULL byte.
        try:
            defer.returnValue(response.data[0])
        except IndexError:
            logger.error("Response from embedded board during discovery sequence did not return data in "
                         "expect format. Expected at least one data field, received: {0}".format(response.data))
            defer.returnValue(None)

    @defer.inlineCallbacks
    def get_property_desc(self, to, requested_property_id):
        """
        Sends a message to the embedded board requesting the property description for a specified
        property ID

        :param to: item ID to send the message to
        :param requested_property_id: property ID to get the description of
        :return:
        """
        try:
            response = yield self.send_command(to, command_id=GET_PROPERTY_DESC, params=["property_id"],
                                               data=[requested_property_id])
        except Exception as e:
            logger.error("[PCOM] Unable to find property description for property {0} in item {1} because of exception:"
                         "{2}".format(requested_property_id, to, e))
            defer.returnValue(None)
        try:
            defer.returnValue(response.data[0])
        except IndexError:
            logger.error("Response from embedded board during discovery sequence did not return data in expect format."
                         " Expected at least one data field, received: {0}".format(response.data))
            defer.returnValue(None)

    @defer.inlineCallbacks
    def get_command_name(self, to, requested_command_id):
        """
        Sends a messge down the serial line requesting the property name of a given property ID,
        used in discovery protocol
        :param to: destination ID
        :param requested_command_id: command ID that we want to know the name of
        :return: name from Embedded Core
        """
        try:
            response = yield self.send_command(to, command_id=GET_COMMAND_NAME, params=["command_id"],
                                               data=[requested_command_id])
        except Exception as e:
            logger.error("[PCOM] Unable to find command name for command {0} in item {1} because of exception:"
                         "{2}".format(requested_command_id, to, e))
            defer.returnValue(None)

        # The data in the response message will be a list,
        # the command name should be in the 0th position
        try:
            defer.returnValue(response.data[0])
        except IndexError:
            logger.error("Response from embedded board during discovery sequence did not return data in expect format."
                         " Expected at least one data field, received: {0}".format(response.data))
            defer.returnValue(None)

    @defer.inlineCallbacks
    def get_command_input_param_format(self, to, requested_command_id):
        """
        Given a command ID and item ID, sends a message to the item ID requesting
        the format of its input parameters. This functions should return a string
        that describes each parameter. NOTE: variable arrays are indicated with a *.
        Eg. A list of ints would be "*i". See format string details for character->byte
        translation.
        :param to: destination item ID
        :param requested_command_id: command ID that we want the parameter format of
        :return: format string describing input parameters
        """
        try:
            response = yield self.send_command(to, command_id=GET_COMMAND_INPUT_PARAM_FORMAT, params=["command_id"],
                                               data=[requested_command_id])
        except Exception as e:
            logger.error("[PCOM] Unable to find command input format for command {0} in item {1} because of exception:"
                         "{2}".format(requested_command_id, to, e))
            defer.returnValue(None)

        r_val = '' if len(response.data) == 0 else response.data[0]
        defer.returnValue(r_val)

    @defer.inlineCallbacks
    def get_command_input_param_names(self, to, requested_command_id):
        """
        Given an item ID and a command ID, requests the parameter names of the command from the item.
        Returns a list of names (comma delimited) that represent the parameter names.

        TODO: change return value to string?

        Eg. "frequency,duty cycle"
        :param to: destination item ID
        :param requested_command_id: command id to find the parameter names of
        :return: a list of parameter names
        """
        try:
            response = yield self.send_command(to, command_id=GET_COMMAND_INPUT_PARAM_NAMES, params=["command_id"],
                                               data=[requested_command_id])
        except Exception as e:
            logger.error("[PCOM] Unable to find command input parameter names for command {0} in item {1} because of "
                         "exception: {2}".format(requested_command_id, to, e))
            defer.returnValue(None)

        param_names = [] if len(response.data) == 0 else [x.strip() for x in response.data[0].split(',')]
        defer.returnValue(param_names)

    @defer.inlineCallbacks
    def get_command_output_parameter_desc(self, to, requested_command_id):
        """
        Given an item ID and a command ID, requests the output description
        Returns a list of names (comma delimited) that represent the output names

        TODO: change return value to string?

        Eg. "frequency,duty cycle"
        :param to: destination item ID
        :param requested_command_id: command id to find the parameter names of
        :return: a list of parameter names
        """
        try:
            response = yield self.send_command(to, command_id=GET_COMMAND_OUTPUT_PARAM_DESC, params=["command_id"],
                                               data=[requested_command_id])
        except Exception as e:
            logger.error("[PCOM] Unable to find command output parameter description for command {0} in item {1}"
                         " because of exception: {2}".format(requested_command_id, to, e))
            defer.returnValue(None)

        list_of_names = [] if len(response.data) == 0 else [x.strip() for x in response.data[0].split(',')]
        defer.returnValue(list_of_names)

    @defer.inlineCallbacks
    def get_property_type(self, to, requested_property_id):
        """
        Given a property ID, requests the property's type from the item ID.
        Gets back a format string.

        :param to: destination item ID
        :param requested_property_id: property ID that we want the type of
        :return: format string describing the type
        """
        try:
            response = yield self.send_command(to, command_id=GET_PROPERTY_TYPE, params=["property_id"],
                                               data=[requested_property_id])
        except Exception as e:
            logger.error("[PCOM] Unable to find property type for property {0} in item {1} because of exception: "
                         "{2}".format(requested_property_id, to, e))
            defer.returnValue(None)

        r_val = '' if len(response.data) == 0 else response.data[0]
        defer.returnValue(r_val)

    def send_command(self, to=None, tx_type="DIRECT", command_id=0, msg_status="INFO", response_req=True, params=[], data=[]):
        """
                Send a command and return a deferred that will succeed on a response and with the response

        :param to: destination item ID
        :param tx_type: DIRECT or BROADCAST
        :param command_id: ID of the command
        :param msg_status: status of the message: ERROR, WARNING, INFO, PROGRESS, or OK
        :param response_req: boolean whether or not a response is required
        :param params: command parameters
        :param data: data that corresponds to each parameter
        :return:
        """
        # Increment the event ID
        event_id = next(self._event_id_generator)

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
            "FROM": self.DISCOVERY_CODE
        }

        if to:
            topics["TO"] = to

        # Build the CONTENTS portion
        contents = {
            "COMMAND": command_id,
        }

        # If data was given via function arguments we need to pack it
        # into the contents portion of the message to resemble a JSON message.
        for parameter, data_val in zip(params, data):
            contents[parameter] = data_val

        # If we need to wait the result should be a deferred object.
        if response_req:
            result = defer.Deferred()
            result.addErrback(self.msg_timeout_errback)
            # Add the correct mapping to the dictionary
            self._discovery_msg_ids[event_id] = result

        # Message will be added to event queue and
        # sent down serial line (via callback function _message_queue_handler())
        self._message_queue.add({"TOPICS": topics, "CONTENTS": contents})

        # Return the Deferred object if we need to
        return result

    def msg_timeout_errback(self, failure):
        """
        Errback attached to a message that is called if it fails to send.

        :param failure:
        :return: defer.failure.Failure object
        """
        return failure

    def connectionMade(self):
        """
        The initializer for the protocol. This function is called when a connection to the server
        (broker in our case) has been established.
        """
        return

    @defer.inlineCallbacks
    def _get_attached_items(self):
        """
        Populates self.items with all attached item IDs
        NOTE: This is a subroutine of the discovery process. This method should be lightweight because
        it also going to be called upon connection establishment. We don't want the user waiting around forever
        when their device is connected.

        :return:
        """

        # If we have stored systems, return them first
        while self._attached_item_d is not None:
            yield self._attached_item_d

        # Create a new deferred object because this is an asynchronous operation.
        self._attached_item_d = defer.Deferred()

        # The first part of the discovery protocol
        # is to fetch all subsystems. The reactor inside of
        # the embedded core should return with each subsystem as a
        # ID, Name pair (eg. (0, "IO_Control_board"))
        try:
            response = yield self.send_command(to=self.BROADCAST_SUBSYSTEM_ID, command_id=0, tx_type="BROADCAST")
            self._subsystem_ids = [int(response.data[0])]
        except Exception as e:
            logger.error("Exception occurred when trying to find available subsystems: {0}".format(e))

        d = self._attached_item_d
        self._attached_item_d = None
        d.callback(None)

    def load_discovery_from_file(self):
        """
        Loads discovery info from PCOMSerial.discovery_file that was passed in at protocol open.

        :return: discovery message that should be sent to broker
        """
        discovery_msg = {}

        try:
            with open(PCOMSerial.discovery_file) as discovery_file:
                data = json.load(discovery_file)
                if len(data) == 0:
                    logger.error("[PCOM] No data loaded from JSON file")
                    discovery_file.close()
                    discovery_msg = self.process_data_file(data)

        except Exception as e:
            logger.error("[PCOM] Could not open discovery file because of exception: {0}".format(e))

        return discovery_msg

    def process_data_file(self, data):
        """
        Given the data from the discovery file, fills in the corresponding maps.
        :param data: data produces from JSON file
        :return: discovery message to produce
        """

        global PCOM_COMMAND_MAP, PCOM_PROPERTY_MAP, PCOM_PROPERTY_NAME_MAP, PCOM_ERROR_CODE_MAP, PCOM_STREAM_NAME_MAP, \
            PCOM_COMMAND_MAP, PCOM_COMMAND_NAME_MAP, PCOM_ITEM_NAME_MAP

        def _convert_item_ids_to_int(_map):
            return {int(k): v for k, v in list(_map.items())}

        def _convert_command_and_prop_ids(_map):
            for k, v in list(_map.items()):
                for command_id, cmd_info in list(v.items()):
                    if command_id.isdigit():
                        _map[k][int(command_id)] = cmd_info
                        del _map[k][command_id]

        PCOM_COMMAND_MAP = data["PCOM_COMMAND_MAP"]
        PCOM_COMMAND_MAP = _convert_item_ids_to_int(PCOM_COMMAND_MAP)
        _convert_command_and_prop_ids(PCOM_COMMAND_MAP)

        PCOM_PROPERTY_MAP = data["PCOM_PROPERTY_MAP"]
        PCOM_PROPERTY_MAP = _convert_item_ids_to_int(PCOM_PROPERTY_MAP)
        _convert_command_and_prop_ids(PCOM_PROPERTY_MAP)

        PCOM_COMMAND_NAME_MAP = data["PCOM_COMMAND_NAME_MAP"]
        PCOM_COMMAND_NAME_MAP = _convert_item_ids_to_int(PCOM_COMMAND_NAME_MAP)

        PCOM_ERROR_CODE_MAP = data["PCOM_ERROR_CODE_MAP"]
        PCOM_ERROR_CODE_MAP = _convert_item_ids_to_int(PCOM_ERROR_CODE_MAP)

        PCOM_PROPERTY_NAME_MAP = data["PCOM_PROPERTY_NAME_MAP"]
        PCOM_PROPERTY_NAME_MAP = _convert_item_ids_to_int(PCOM_PROPERTY_NAME_MAP)

        PCOM_STREAM_NAME_MAP = data["PCOM_STREAM_NAME_MAP"]
        PCOM_STREAM_NAME_MAP = _convert_item_ids_to_int(PCOM_STREAM_NAME_MAP)

        PCOM_ITEM_NAME_MAP = data["PCOM_ITEM_NAME_MAP"]
        PCOM_ITEM_NAME_MAP = _convert_item_ids_to_int(PCOM_ITEM_NAME_MAP)

        discovery_msg = data["DISCOVERY"]

        for item in discovery_msg["CHILDREN"]:
            self.adapter.subscribe(self.add_message_to_queue, TO=item["ID"])

        return discovery_msg

    def write_discovery_info_to_file(self, file_name, discovery_msg):
        """
        Given a discovery message and file name, writes the necessary discovery information to the file.

        This includes several maps and the discovery message itself so that it does not need to be generated.

        :param file_name: discovery file name
        :param discovery_msg: discovery message to be pushed to broker
        :return:
        """
        try:
            discovery_file = open(file_name, "w")
        except Exception as e:
            logger.error("Could not open file: {0} because of exception: {1}".format(file_name, e))
            return

        dict_to_write = dict()
        dict_to_write["PCOM_COMMAND_MAP"] = PCOM_COMMAND_MAP
        dict_to_write["PCOM_PROPERTY_MAP"] = PCOM_PROPERTY_MAP
        dict_to_write["PCOM_COMMAND_NAME_MAP"] = PCOM_COMMAND_NAME_MAP
        dict_to_write["PCOM_ERROR_CODE_MAP"] = PCOM_ERROR_CODE_MAP
        dict_to_write["PCOM_PROPERTY_MAP"] = PCOM_PROPERTY_MAP
        dict_to_write["PCOM_PROPERTY_NAME_MAP"] = PCOM_PROPERTY_NAME_MAP
        dict_to_write["PCOM_STREAM_NAME_MAP"] = PCOM_STREAM_NAME_MAP
        dict_to_write["PCOM_ITEM_NAME_MAP"] = PCOM_ITEM_NAME_MAP
        dict_to_write["DISCOVERY"] = discovery_msg
        json.dump(dict_to_write, discovery_file)
        logger.info("Discovery written to: {0}".format(file_name))
        discovery_file.close()

    @staticmethod
    def build_command_info(format, input_params, output_params):
        """
        Builds the command info (dictionary) for a command when given the format, input parameters,
        and output parameters.

        :param format: format string (eg. "fff")
        :param input_params: list of parameter names (eg. ["rate", "height", "weight"])
        :param output_params: list of output parameter names (eg. ["Rick", "and", "Morty"])
        :return: the command info dictionary
        """
        return {"format": format, "input params": input_params, "output params": output_params}

    @staticmethod
    def initialize_command_maps(item_id):
        """
        Creates the discovery command entries in the command map for the specified item ID.
        :param item_id: Item ID found during discovery.
        :return: None
        """

        # initialize the maps for this item
        PCOM_COMMAND_MAP[item_id] = {}
        PCOM_PROPERTY_MAP[item_id] = {}
        PCOM_COMMAND_NAME_MAP[item_id] = {}
        PCOM_PROPERTY_NAME_MAP[item_id] = {}
        PCOM_STREAM_NAME_MAP[item_id] = {}

        PCOM_COMMAND_MAP[item_id][RESET_ITEM] = PCOMSerial.build_command_info("", [], [])
        PCOM_COMMAND_MAP[item_id][GET_ITEM_NAME] = PCOMSerial.build_command_info("", [], ["Item name"])
        PCOM_COMMAND_MAP[item_id][GET_ITEM_TYPE] = PCOMSerial.build_command_info("", [], ["Item type"])
        PCOM_COMMAND_MAP[item_id][GET_COMMAND_IDS] = PCOMSerial.build_command_info("", [], ["Command IDs[]"])
        PCOM_COMMAND_MAP[item_id][GET_PROPERTY_IDS] = PCOMSerial.build_command_info("", [], ["Property IDs[]"])
        PCOM_COMMAND_MAP[item_id][GET_COMMAND_NAME] = \
            PCOMSerial.build_command_info("H", ["command_id"], ["Command name"])
        PCOM_COMMAND_MAP[item_id][GET_COMMAND_INPUT_PARAM_FORMAT] = \
            PCOMSerial.build_command_info("H", ["command_id"], ["Command input format"])
        PCOM_COMMAND_MAP[item_id][GET_COMMAND_INPUT_PARAM_NAMES] = \
            PCOMSerial.build_command_info("H", ["command_id"], ["Command input names[]"])
        PCOM_COMMAND_MAP[item_id][GET_COMMAND_OUTPUT_PARAM_DESC] = \
            PCOMSerial.build_command_info("H", ["command_id"], ["Command input output description"])
        PCOM_COMMAND_MAP[item_id][GET_PROPERTY_NAME] = \
            PCOMSerial.build_command_info("H", ["property_id"], ["Property name"])
        PCOM_COMMAND_MAP[item_id][GET_PROPERTY_TYPE] = \
            PCOMSerial.build_command_info("H", ["property_id"], ["Property type"])
        PCOM_COMMAND_MAP[item_id][GET_PROPERTY_DESC] = \
            PCOMSerial.build_command_info("H", ["property_id"], ["Property desc"])

        PCOM_COMMAND_MAP[item_id]["reset_item"] = RESET_ITEM
        PCOM_COMMAND_MAP[item_id]["get_item_name"] = GET_ITEM_NAME
        PCOM_COMMAND_MAP[item_id]["get_item_type"] = GET_ITEM_TYPE
        PCOM_COMMAND_MAP[item_id]["get_command_ids"] = GET_COMMAND_IDS
        PCOM_COMMAND_MAP[item_id]["get_property_ids"] = GET_PROPERTY_IDS
        PCOM_COMMAND_MAP[item_id]["get_command_name"] = GET_COMMAND_NAME
        PCOM_COMMAND_MAP[item_id]["get_command_input_param_format"] = GET_COMMAND_INPUT_PARAM_FORMAT
        PCOM_COMMAND_MAP[item_id]["get_command_input_param_names"] = GET_COMMAND_INPUT_PARAM_NAMES
        PCOM_COMMAND_MAP[item_id]["get_command_input_param_names"] = GET_COMMAND_INPUT_PARAM_NAMES
        PCOM_COMMAND_MAP[item_id]["get_command_output_param_desc"] = GET_COMMAND_OUTPUT_PARAM_DESC
        PCOM_COMMAND_MAP[item_id]["get_property_name"] = GET_PROPERTY_NAME
        PCOM_COMMAND_MAP[item_id]["get_property_type"] = GET_PROPERTY_TYPE
        return

    @defer.inlineCallbacks
    def get_discovery(self):
        """
        Hitting the "discovery" button on the UI triggers this generator.

        Run a discovery for everything connected to this protocol and return a list of of all connected:
        items, messages, and endpoint types
        """

        if not PCOMSerial.is_port_attached:
            logger.error("[PCOM] Failed to discover. No port connected.")
            self.send_command(tx_type="BROADCAST", msg_status="ERROR",
                              data=["No Serial Port connected to Parlay. Open serial port before discovering"])
            defer.returnValue(BaseProtocol.get_discovery(self))

        self._subsystem_ids = []
        # If we were already in the process of a discovery we should
        # return a deferred object.
        if self._discovery_in_progress:
            defer.returnValue(self._discovery_deferred)

        self._discovery_in_progress = True

        self._get_attached_items()

        if PCOMSerial.discovery_file is not None:
            discovery_msg = self.load_discovery_from_file()
            if discovery_msg != {}:
                self._loaded_from_file = True
                self._discovery_in_progress = False
                defer.returnValue(discovery_msg)

        self._loaded_from_file = False

        logger.info("Unable to load discovery from file, fetching items from embedded system...")

        t1 = time.time()

        # If there is a deferred item, yield that first
        if self._attached_item_d is not None:
            yield self._attached_item_d

        self.items = []
        for subsystem_id in self._subsystem_ids:
            try:
                yield self._get_item_discovery_info(subsystem_id)
            except Exception as e:
                logger.error("Exception while discovering! Skipping subsystem: {0}\n     {1}".format(subsystem_id, e))

        self._discovery_in_progress = False

        t2 = time.time()

        logger.info("Discovery took {0} seconds".format(str(t2-t1)))
        # At this point self.items should be populated with
        # the ParlayStandardItem objects that represent the items we discovered.
        # By calling BaseProtocol's get_discovery() function we can get that information
        # to the adapter and furthermore to the broker.
        discovery_msg = BaseProtocol.get_discovery(self)

        if PCOMSerial.discovery_file is not None and self._loaded_from_file is False:
            self.write_discovery_info_to_file(PCOMSerial.discovery_file, discovery_msg)

        if self._discovery_deferred:
            self._discovery_deferred.callback(discovery_msg)

        # reset discovery deferred
        self._discovery_deferred = defer.Deferred()

        defer.returnValue(discovery_msg)


    @staticmethod
    def command_cb(command_info_list, item_id, command_id, command_dropdowns, command_subfields, parlay_item,
                   hidden=False):
        """
        Callback function used to update the command map and parlay item dropdown menu when the command info
        is retrieved from the embedded device during discovery. This function is called using gatherResults
        which will only callback once all deferreds have been fired.

        :param command_info_list: Stores the command name, command input format, command input names
        and command output description. This information will be used to populate the ParlayStandardItem.
        :param item_id: 2 byte item ID of the ParlayStandardItem we will be populating
        :param command_id: 2 byte command ID of the command we have the information for
        :param command_dropdowns: dropdown field for the ParlayStandardItem
        :param command_subfields: subfields for each dropdown option of the ParlayStandardItem
        :param parlay_item: ParlayStandardItem that we will be updating
        :param hidden: whether or not the command will be hidden from UI
        :return:
        """
        expected_command_info_list_length = 4

        local_subfields = []

        # Ensure that the command_info_list contains at least <expected #> fields
        if len(command_info_list) < expected_command_info_list_length:
            logger.error("Error in discovering command information for:"
                         "-- Item: {0}\n"
                         "-- Command: {1}".format(item_id, command_id))

        c_name = command_info_list[0]
        c_input_format = expand_fmt_string(command_info_list[1])
        c_input_names = command_info_list[2]
        c_output_desc = command_info_list[3]

        PCOM_COMMAND_MAP[item_id][command_id] = PCOMSerial.build_command_info(c_input_format, c_input_names,
                                                                              c_output_desc)

        PCOM_COMMAND_NAME_MAP[item_id][c_name] = command_id

        if not hidden:
            command_dropdowns.append((c_name, command_id))

            PCOMSerial.build_parlay_item_subfields(c_input_format, c_input_names, local_subfields, parlay_item)

            command_subfields.append(local_subfields)
        return

    @staticmethod
    def build_property_data(name, fmt):
        """
        Builds property dictionary consisting of the name and format of the property.

        :param name: property name (eg. "Butterbot")
        :param fmt: format string for the property (eg. "f")
        :return: dictionary representing information about the property
        """
        return {"name": name, "format": fmt}

    @staticmethod
    def tokenize_format_char_string(format_chars):
        """
        Given a format string, returns a list of tokens.

        Eg.

        "bB*i" -> ["b", "B", "*i"]
        :param format_chars:
        :return:
        """

        token = ""
        tokenized_list = []
        for i in format_chars:
            token += i
            if token != "*":
                tokenized_list.append(token)
            token = ""
        return tokenized_list

    @staticmethod
    def build_parlay_item_subfields(c_input_format, c_input_names, local_subfields, parlay_item):
        """
        Builds up Parlay items to be pushed to discoverer

        :param c_input_format: format string Eg. "bBIq"
        :param c_input_names: list of string names representing the input parameters Eg. ["hello", "hello"]
        :param local_subfields: subfields of the Parlay item
        :param parlay_item: the Parlay item to be built up
        :return:
        """

        format_tokens = PCOMSerial.tokenize_format_char_string(c_input_format)

        for parameter, format_token in zip(c_input_names, expand_fmt_string(format_tokens)):
            local_subfields.append(parlay_item.create_field(msg_key=parameter, label=parameter,
                                                            input=PCOMSerial._get_input_type(format_token), required=True))

    @staticmethod
    def _get_input_type(format_char):
        """
         Given a format character, returns the corresponding Parlay input type

         :param format_char:
         :return:
         """

        if len(format_char) == 0:
            return INPUT_TYPES.STRING
        if len(format_char) > 1:
            return INPUT_TYPES.ARRAY

        if format_char in PCOM_SERIAL_NUMBER_INPUT_CHARS:
            return INPUT_TYPES.NUMBER
        elif format_char[0] == PCOM_SERIAL_ARRAY_INPUT_CHARS:
            return INPUT_TYPES.ARRAY
        elif format_char in PCOM_SERIAL_STRING_INPUT_CHARS:
            return INPUT_TYPES.STRING
        else:
            logger.warn("Invalid format character {0} defaulting to INPUT TYPE STRING".format(format_char))

        return INPUT_TYPES.STRING

    @staticmethod
    def property_cb(property_info_list, item_id, property_id, parlay_item):
        """

        Callback function that populates the ParlayStandardItem parlay_item with
        the designated property information.

        :param property_info_list: Property name and property type obtained from the embedded device
        :param item_id: 2 byte item ID that we will be populating the ParlayStandardItem of
        :param property_id: 2 byte property ID that represents the property we will updating
        :param parlay_item: ParlayStandardItem object
        :return:
        """

        expected_property_info_list_length = 3

        if len(property_info_list) < expected_property_info_list_length:
            logger.error("Error in discovering property information for:"
                         "-- Item: {0}\n"
                         "-- Property: {1}".format(item_id, property_id))

        # set variable to positions in the list for readability
        property_name = property_info_list[0]
        property_type = property_info_list[1]

        PCOM_PROPERTY_NAME_MAP[item_id][property_name] = property_id
        PCOM_STREAM_NAME_MAP[item_id][property_name] = property_id

        PCOM_PROPERTY_MAP[item_id][property_id] = PCOMSerial.build_property_data(property_name, property_type)

        parlay_item.add_property(property_id, name=property_name, attr_name=property_name, input=PCOMSerial._get_input_type(property_type))
        parlay_item.add_datastream(property_name, name=property_name, attr_name=property_name)

        return

    def _initialize_reactor_command_map(self, reactor):
        """
        Inserts entries for the reactor into the PCOM_COMMAND_MAP used for message translation.

        :param reactor: reactor ID
        :return: None
        """
        PCOM_COMMAND_MAP[reactor] = {}
        PCOM_COMMAND_MAP[reactor][0] = PCOMSerial.build_command_info("", [], [])
        PCOM_COMMAND_MAP[reactor][GET_ERROR_CODES] = PCOMSerial.build_command_info("", [], ["codes"])
        PCOM_COMMAND_MAP[reactor][GET_ERROR_STRING] = PCOMSerial.build_command_info("H", ["code"], ["string"])

    @defer.inlineCallbacks
    def _get_item_discovery_info(self, subsystem):
        """
        The discovery protocol for the embedded core:

        GET SUBSYSTEMS IDS
        GET ITEM IDS
        GET ERROR CODE INFO
        GET ITEM INFORMATION

        Where ITEM INFORMATION is:
            GET ITEM NAME
            GET ITEM TYPE
            GET COMMAND IDS
            GET PROPERTY IDS

            For each command:
                GET COMMAND NAME
                GET COMMAND INPUT PARAM FORMAT
                GET COMMAND INPUT PARAM NAMES
                GET COMMAND OUTPUT PARAM DESCRIPTION

            For each property:
                GET PROPERTY NAME
                GET PROPERTY TYPE

        :param subsystem: The subsystem we will be getting the item IDs from
        :return:
        """

        # Subsystem ID is the high byte of the item ID
        subsystem_id = subsystem

        # If the item was already discovered attach nothing to
        # the Deferred object's return value.
        # Otherwise add the item ID to the set of already discovered
        # IDs because we are about to discover it!

        # if item_id in self._already_discovered:
        #     defer.returnValue({})
        # else:
        #     # add the item ID to the already_discovered set.
        #     self._already_discovered.add(item_id)

        discovery = {"Subsystem ID": subsystem_id}
        logger.info("Running discovery on subsystem: {0}".format(subsystem_id))

        # Convert subsystem IDs to ints so that we can send them
        # back down the serial line to retrieve their attached item
        # For each subsystem ID, fetch the items attached to it

        logger.info("Fetching items from subsystem ID: {0}".format(subsystem_id))

        REACTOR = subsystem_id << self.SUBSYSTEM_SHIFT
        # Fetch error codes
        self._initialize_reactor_command_map(REACTOR)
        PCOM_ITEM_NAME_MAP[REACTOR] = "REACTOR"
        try:
            response = yield self.send_command(REACTOR, "DIRECT")
            self._item_ids = [int(item_id) for item_id in response.data]

            response = yield self.send_command(to=REACTOR, tx_type="DIRECT", command_id=GET_ERROR_CODES)
            self._error_codes = [int(error_code) for error_code in response.data]

            for error_code in self._error_codes:
                response = yield self.send_command(to=REACTOR, tx_type="DIRECT", command_id=GET_ERROR_STRING,
                                                   params=["code"], data=[error_code])
                error_code_string = response.data[0]
                PCOM_ERROR_CODE_MAP[error_code] = error_code_string

            logger.info("---> ITEM IDS FOUND: {0}".format(self._item_ids))

            for item_id in self._item_ids:
                self.adapter.subscribe(self.add_message_to_queue, TO=item_id)
                PCOMSerial.initialize_command_maps(item_id)

            for item_id in self._item_ids:
                response = yield self.send_command(item_id, command_id=GET_ITEM_NAME, tx_type="DIRECT")
                item_name = str(response.data[0])

                PCOM_ITEM_NAME_MAP[item_id] = item_name
                parlay_item = ParlayStandardItem(item_id=item_id, name=item_name)

                response = yield self.send_command(item_id, command_id=GET_ITEM_TYPE, tx_type="DIRECT")

                item_type = int(response.data[0])

                response = yield self.send_command(item_id, command_id=GET_COMMAND_IDS, tx_type="DIRECT")

                command_ids = response.data

                command_dropdowns = []
                command_subfields = []

                parlay_item.add_field('COMMAND', INPUT_TYPES.DROPDOWN,
                                      dropdown_options=command_dropdowns,
                                      dropdown_sub_fields=command_subfields)

                def placeholder(failure):
                    return failure

                discovered_command = defer.DeferredList([])
                discovered_command.addErrback(placeholder)

                for command_id in command_ids:
                    # Loop through the command IDs and build the Parlay Item object
                    # for each one

                    command_name = self.get_command_name(item_id, command_id)
                    command_input_format = self.get_command_input_param_format(item_id, command_id)
                    command_input_param_names = self.get_command_input_param_names(item_id, command_id)
                    command_output_desc = self.get_command_output_parameter_desc(item_id, command_id)

                    if not command_name or not command_input_format or not command_input_param_names or \
                            not command_output_desc:

                        discovered_command.errback(defer.failure.Failure(Exception("")))
                        raise Exception("[PCOM] Unable to fetch command info for item:", item_id)

                    discovered_command = defer.gatherResults([command_name, command_input_format,
                                                              command_input_param_names, command_output_desc])
                    discovered_command.addCallback(PCOMSerial.command_cb, item_id=item_id, command_id=command_id,
                                                   command_subfields=command_subfields,
                                                   command_dropdowns=command_dropdowns,
                                                   parlay_item=parlay_item, hidden=(command_id in DISCOVERY_MESSAGES))

                yield discovered_command

                response = yield self.send_command(item_id, command_id=GET_PROPERTY_IDS, tx_type="DIRECT")
                property_ids = response.data

                discovered_property = defer.DeferredList([])
                discovered_property.addErrback(placeholder)

                for property_id in property_ids:

                    property_name = self.get_property_name(item_id, property_id)
                    property_type = self.get_property_type(item_id, property_id)
                    property_desc = self.get_property_desc(item_id, property_id)

                    if not property_name or not property_type or not property_desc:
                        discovered_property.errback(defer.failure.Failure(Exception("")))
                        raise Exception("[PCOM] Unable to fetch property info for item:", item_id)

                    discovered_property = defer.gatherResults([property_name, property_type, property_desc])
                    discovered_property.addCallback(PCOMSerial.property_cb, item_id=item_id, property_id=property_id,
                                                    parlay_item=parlay_item)

                yield discovered_property

                if item_type != ITEM_TYPE_HIDDEN:
                    self.items.append(parlay_item)

                logger.info("[PCOM] Finished ITEM: {0}".format(item_name))

        except Exception as e:
            logger.error("[PCOM]: Could not fetch discovery info due to exception: {0}".format(e))
            raise e

        logger.error("[PCOM] Finished subsystem: {0}".format(subsystem))
        defer.returnValue(discovery)

    def _send_broadcast_message(self):
        """
        Sends broadcast message to the broadcast subsystem ID stored in
        self.BROADCAST_SUBSYSTEM_ID
        :return:
        """

        # The subsystem ID for the broadcast message is 0x80
        # The high byte of the destination ID is the subsystem ID, so we should insert
        # 0x80 into the high byte.
        destination_id = self.BROADCAST_SUBSYSTEM_ID + self.EMBD_REACTOR_ID

        # The response code, event type, event attributes, and format string are all zero for a
        # broadcast message
        return self.send_command(to=destination_id, command_id=0, tx_type="BROADCAST")

    def add_message_to_queue(self, message):
        """
        This will send a packet down the serial line. Subscribe to messages using the adapter

        :param message : A parlay dictionary message
        """

        # add the message to the queue
        self._message_queue.add(message)

    def rawDataReceived(self, data):
        """
        This function is called whenever data appears on the serial port and raw mode is turned on.
        Since this protocol uses line receiving, this function should never be called, so raise an
        excpetion if it is.

        :param data:
        :return:
        """

        raise Exception('Using line received!')

    def _is_reset_msg(self, msg):

        if "CONTENTS" in msg:
            if "EVENT" in msg["CONTENTS"]:
                if msg["CONTENTS"]["EVENT"] == self.PCOM_RESET_ID:
                    return True

        return False

    def _on_packet(self, sequence_num, ack_expected, is_ack, is_nak, msg):
        """
        This will get called with every new serial packet.
        The parameters are the expanded tuple given from unstuff_packet
        :param sequence_num: the sequence number of the received packet
        :param ack
        _expected: Is an ack expected to this message?
        :param is_ack : Is this an ack?
        :param is_nak: Is this a nak?
        :param msg: The pcom message (if it is one and not an ack/nak)
        :type msg : PCOMMessage
        """

        if is_ack:
            self._ack_window.remove(sequence_num)
            return
        elif is_nak:
            return  # Ignore, timeout should handle the resend.

        parlay_msg = msg.to_json_msg()
        if self._is_reset_msg(parlay_msg):
            self.reset()
            logger.info("[PCOM] Reset message received! Resetting... ")

        # If we need to ack, ACK!
        if ack_expected:
            ack = str(p_wrap(ack_nak_message(sequence_num, True)))
            self.transport.write(ack)

        self.adapter.publish(parlay_msg, self.transport.write)

        # also send it to discovery listener locally
        self._discovery_listener(msg)

    def lineReceived(self, line):
        """
        If this function is called we have received a <line> on the serial port
        that ended in 0x03.

        :param line:
        :return:
        """
        # Using byte array so unstuff can use numbers instead of strings
        buf = bytearray()
        start_byte_index = (line.rfind(START_BYTE_STR) + 1)
        buf += line
        try:
            packet_tuple = unstuff_packet(buf[start_byte_index:])
            self._on_packet(*packet_tuple)
        except FailCRC:
            logger.error("[PCOM] Failed CRC")
        except Exception as e:
            logger.error("[PCOM] Could not decode message because of exception: {0}".format(e))


class ACKInfo:
    """
    Stores ACK information: deferred and number of retries
    """

    def __init__(self, sequence_number, num_retries, packet, transport, failure_function, msg_deferred):
        self.deferred = defer.Deferred()
        self.num_retries = num_retries
        self.sequence_number = sequence_number
        self.transport = transport
        self.packet = packet
        self.failure_function = failure_function
        self.msg_deferred = msg_deferred


class SlidingACKWindow:
    """
    Represents an ACK window
    """

    TIMEOUT = 1000
    EXPIRED = 1001

    def __init__(self, window_size, num_retries):
        self._window = {}
        self._queue = []
        self.WINDOW_SIZE = window_size
        self.NUM_RETRIES = num_retries
        self.MAX_ACK_SEQ = 16
        # Initialize lack_acked_map so that none of the first ACKs think they are
        # duplicates. -1 works because no ACK has sequence number -1
        self._last_acked_map = {seq_num: -1 for seq_num in range(self.MAX_ACK_SEQ/2)}

    def ack_received_callback(self, sequence_number):
        """
        Callback for the deferred objects in the sliding ACK window.

        When an ACK is received we should remove it from the window and then
        move one ACK from the queue into the window
        :return:
        """

        # Check for duplicate ack
        if sequence_number != self.TIMEOUT:

            if sequence_number != self.EXPIRED:
                if self._last_acked_map[sequence_number % self.WINDOW_SIZE] == sequence_number:
                    logger.error("[PCOM] UNEXPECTED ACK SEQ NUM: {0} DROPPING".format(sequence_number))
                    return

                self._last_acked_map[sequence_number % self.WINDOW_SIZE] = sequence_number

                del self._window[sequence_number]
                if len(self._queue) > 0:
                    ack_to_add = self._queue.pop(0)
                    self.add_to_window(ack_to_add)

    def reset_window(self):

        # remove all deferreds
        for seq_num in self._window:
            self._window[seq_num].deferred.callback(seq_num)

        self._window = {}
        self._queue = []
        self._last_acked_map = {seq_num: -1 for seq_num in range(self.MAX_ACK_SEQ/2)}

    def ack_timeout_errback(self, timeout_failure):
        """
        Errback that is called on ACK timeout
        :param timeout_exception: TimeoutException object that holds the ACK sequence number that timed out
        :return:
        """

        ack_to_send = self._window[timeout_failure.value.sequence_number]
        if ack_to_send.num_retries < self.NUM_RETRIES:
            logger.warn("[PCOM] TIMEOUT SEQ NUM {0} RESENDING...".format(timeout_failure.value.sequence_number))
            ack_to_send.transport.write(ack_to_send.packet)
            ack_to_send.num_retries += 1
            d = defer.Deferred()
            d.addErrback(self.ack_timeout_errback)
            d.addCallback(self.ack_received_callback)
            ack_to_send.deferred = d
            self.ack_timeout(ack_to_send.deferred, PCOMSerial.ACK_TIMEOUT, ack_to_send.sequence_number)
            return self.TIMEOUT

        if self._window[timeout_failure.value.sequence_number].msg_deferred:
            self._window[timeout_failure.value.sequence_number].msg_deferred.errback(defer.failure.Failure
                                                                                     (Exception('Timeout Error')))
        self._window[timeout_failure.value.sequence_number].failure_function()
        del self._window[timeout_failure.value.sequence_number]
        return self.EXPIRED

    def add_to_window(self, ack_info):
        """
        Adds <ack_info> to the window
        :param ack_info: ACKInfo object that will be added
        :return:
        """

        ack_info.deferred.addCallback(self.ack_received_callback)
        ack_info.deferred.addErrback(self.ack_timeout_errback)

        ack_info.transport.write(ack_info.packet)
        self.ack_timeout(ack_info.deferred, PCOMSerial.ACK_TIMEOUT, ack_info.sequence_number)
        self._window[ack_info.sequence_number] = ack_info

    def add(self, ack_info):
        """
        Adds ack_info to the window if there is room, or to the queue if there isn't any room
        :param ack_info: ACKInfo object
        :return:
        """

        if len(self._window) < self.WINDOW_SIZE:
            self.add_to_window(ack_info)
        else:
            self._queue.append(ack_info)

    def remove(self, sequence_number):
        """
        Removes ack_info from the window
        :param sequence_number: sequence number of the ACK to remove from window
        :return:
        """
        if sequence_number in self._window:
            self._window[sequence_number].deferred.callback(sequence_number)

    def ack_timeout(self, d, seconds, sequence_number):
        """
        An extension of the timeout() function from Parlay utils. Calls the errback of d
        in <seconds> seconds if d is not called. In this case we will be passing a TimeoutException
        with the ACK sequence number so that we can remove it from the table.
        """
        if seconds is None:
            return d

        def cancel():
            if not d.called:
                d.errback(TimeoutException(sequence_number))

        timer = reactor.callLater(seconds, cancel)

        # clean up the timer on success
        def clean_up_timer(result):
            if timer.active():
                timer.cancel()
            return result  # pass through the result
        d.addCallback(clean_up_timer)


class TimeoutException(Exception):
    """
    A custom exception used to be passed to the timeout errback for ACKs.
    The sequence number needs to be stored so that the errback can lookup the correct
    ACK in the sliding window.
    """

    def __init__(self, sequence_number):
        self.sequence_number = sequence_number




