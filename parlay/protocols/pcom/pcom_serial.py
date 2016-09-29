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
from parlay.protocols.utils import message_id_generator, timeout, MessageQueue, TimeoutError, delay

from serial.tools import list_ports

from serial_encoding import *
from enums import *

from collections import namedtuple

import time



# A namedtuple representing the information of each property.
# This information will be retrieved during discovery.
# name = string representing the name of the property
# format = format describing the type of property.
# Eg. If the property were a floating point value it would be 'f'

PropertyData = namedtuple('PropertyData', 'name format')

# Store a map of Item IDs -> Command ID -> Command Objects
# Command objects will store the parameter -> format mapping
command_map = {}

# Store a map of properties. We must keep track of a
# name -> format mapping in order to serialize data
property_map = {}

# Store a map of command names to IDs
# item ID -> Command name -> ID
command_name_map = {}

# Store a map of property names to IDs
# item ID -> Property name -> ID
property_name_map = {}

# Map of error codes to their string name equivalent
error_code_map = {}


class PCOMSerial(BaseProtocol, LineReceiver):

    # Constant number of retries before another message is sent out
    # after not receiving an ACK
    NUM_RETRIES = 3

    # The item ID of the protocol during discovery.
    DISCOVERY_CODE = 0xfefe

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

    # Number of bits we have for sequence number
    SEQ_BITS = 4

    # baud rate of communication over serial line
    BAUD_RATE = 115200

    # ACK window size
    WINDOW_SIZE = 8

    ACK_DIFFERENTIAL = 8

    # timeout before resend in secs
    ACK_TIMEOUT = .5


    @classmethod
    def open(cls, adapter, port):
        """
        :param cls: The class object
        :param adapter: current adapter instance used to interface with broker
        :param port: the serial port device to use.
        :param baudrate: the baudrate that will be set by user.
        :return: returns the instantiated protocol object
        '"""
        # Make sure port is not a list
        port = port[0] if isinstance(port, list) else port
        protocol = PCOMSerial(adapter)
        SerialPort(protocol, port, adapter.reactor, baudrate=cls.BAUD_RATE)
        return protocol

    @classmethod
    def get_open_params_defaults(cls):
        """
        Returns a list of parameters defaults. These will be displayed in the UI.
        :return: default args: the default arguments provided to the user in the UI
        """

        default_args = BaseProtocol.get_open_params_defaults()
        potential_serials = [port_list[0] for port_list in list_ports.comports()]
        default_args['port'] = potential_serials

        return default_args

    def close(self):
        """
        Simply close the connection
        :return:
        """

        self.transport.loseConnection()
        return defer.succeed(None)

    def __init__(self, adapter):
        """
        :param adapter: The adapter that will serve as an interface for interacting with the broker
        """
        # A list of items that we will need to discover for.
        # The base protocol will use this dictionary to feed items to
        # the UI
        self.items = []
        self._error_codes = []

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

        self._in_progress = False
        self._discovery_deferred = defer.Deferred()

        self._ack_table = {seq_num : defer.Deferred() for seq_num in range(2**self.SEQ_BITS)}

        self._ack_window = SlidingACKWindow(self.WINDOW_SIZE, self.NUM_RETRIES)

    def send_error_message(self, original_message, message_status):
        """
        Sends a notification error to the destination ID.

        :param original_message: PCOM Message object that holds the IDs of the sender and receiver
        :param message_status: Message status code that translates to an error message.
        :return:
        """

        error_msg = pcom_message.PCOMMessage(to=original_message.from_, from_=original_message.to,
                                             msg_status=message_status, msg_id=original_message.msg_id)

        json_msg = error_msg.to_json_msg()
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
        d.callback(None)

        # print "MESSAGE", message
        s = pcom_message.PCOMMessage.from_json_msg(message)

        # Serialize the message and prepare for protocol wrapping.
        try:
            packet = encode_pcom_message(s)
        except:
            self.send_error_message(original_message=s, message_status="Unable to encode message: {0}".format(s))
            return d

        need_ack = True

        # Get the next sequence number and then wrap the protocol with
        # the desired low level byte wrapping and send down serial line
        sequence_num = self._seq_num.next()
        packet = str(wrap_packet(packet, sequence_num, need_ack))

        # print "SENT MESSAGE: ", [hex(ord(x)) for x in packet]


        # Write to serial line! Good luck packet.
        self._ack_window.add(ACKInfo(sequence_num, 0, packet, self.transport))
        return d

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

        response = yield self.send_command(to, command_id=GET_PROPERTY_NAME, params=['0'],
                                           data=[requested_property_id])

        # The data in the response message will be a list,
        # the property name should be in the 0th position
        # and strip the NULL byte.
        defer.returnValue(response.data[0])

    @defer.inlineCallbacks
    def get_command_name(self, to, requested_command_id):
        """
        Sends a messge down the serial line requesting the property name of a given property ID,
        used in discovery protocol
        :param to: destination ID
        :param requested_command_id: command ID that we want to know the name of
        :return: name from Embedded Core
        """

        response = yield self.send_command(to, command_id=GET_COMMAND_NAME, params=['0'],
                                           data=[requested_command_id])

        # The data in the response message will be a list,
        # the command name should be in the 0th position
        defer.returnValue(response.data[0])

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

        response = yield self.send_command(to, command_id=GET_COMMAND_INPUT_PARAM_FORMAT, params=['0'],
                                           data=[requested_command_id])

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

        response = yield self.send_command(to, command_id=GET_COMMAND_INPUT_PARAM_NAMES, params=['0'],
                                           data=[requested_command_id])

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

        response = yield self.send_command(to, command_id=GET_COMMAND_OUTPUT_PARAM_DESC, params=['0'],
                                           data=[requested_command_id])
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

        response = yield self.send_command(to, command_id=GET_PROPERTY_TYPE, params=['0'],
                                           data=[requested_property_id])

        r_val = '' if len(response.data) == 0 else response.data[0]
        defer.returnValue(r_val)

    def send_command(self, to, tx_type="DIRECT", command_id=0, msg_status="INFO", response_req=True, params=[], data=[]):
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
            "FROM": self.DISCOVERY_CODE,
            "TO": to
        }

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
            # Add the correct mapping to the dictionary
            self._discovery_msg_ids[event_id] = result

        # Message will be added to event queue and
        # sent down serial line (via callback function _message_queue_handler())
        self._message_queue.add({"TOPICS": topics, "CONTENTS": contents})

        # Return the Deferred object if we need to
        return result

    def connectionMade(self):
        """
        The initializer for the protocol. This function is called when a connection to the server
        (broker in our case) has been established. Keep this function LIGHT, it should not take a long time
        to fetch the subsystem and item IDs. The user typically shouldn't notice.

        I wrote a function _get_attached_items() that is called here and also when a discovery takes place.
        :return: None
        """

        self._get_attached_items()
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

        print "Getting attached subsystems"
        # If we have stored systems, return them first
        while self._attached_item_d is not None:
            yield self._attached_item_d

        # Create a new deferred object because this is an asynchronous operation.
        self._attached_item_d = defer.Deferred()

        # The first part of the discovery protocol
        # is to fetch all subsystems. The reactor inside of
        # the embedded core should return with each subsystem as a
        # ID, Name pair (eg. (0, "IO_Control_board"))
        print "Sending broadcast"
        response = yield self.send_command(to=self.BROADCAST_SUBSYSTEM_ID, command_id=0, tx_type="BROADCAST")
        self._subsystem_ids = [int(response.data[0])]
        print "Subsystems found:", response.data[1]

        # TODO: Explain this in comments
        d = self._attached_item_d
        self._attached_item_d = None
        d.callback(None)

    @staticmethod
    def initialize_command_maps(item_id):
        """
        Creates the discovery command entries in the command map for the specified item ID.
        :param item_id: Item ID found during discovery.
        :return: None
        """

        command_map[item_id] = {}
        property_map[item_id] = {}
        command_name_map[item_id] = {}
        property_name_map[item_id] = {}

        command_map[item_id][RESET_ITEM] = CommandInfo("", "", "")
        command_map[item_id][GET_ITEM_NAME] = CommandInfo("", [], ["Item name"])
        command_map[item_id][GET_ITEM_TYPE] = CommandInfo("", [], ["Item type"])
        command_map[item_id][GET_COMMAND_IDS] = CommandInfo("", [], ["Command IDs"])
        command_map[item_id][GET_PROPERTY_IDS] = CommandInfo("", [], ["Property IDs"])
        command_map[item_id][GET_COMMAND_NAME] = CommandInfo("H", ["command_id"], ["Command name"])
        command_map[item_id][GET_COMMAND_INPUT_PARAM_FORMAT] = CommandInfo("H", ["command_id"],
                                                                           ["Command input format"])
        command_map[item_id][GET_COMMAND_INPUT_PARAM_NAMES] = CommandInfo("H", ["command_id"], ["Command input names"])
        command_map[item_id][GET_COMMAND_OUTPUT_PARAM_DESC] = CommandInfo("H", ["command_id"], ["Command output names"])
        command_map[item_id][GET_PROPERTY_NAME] = CommandInfo("H", ["property_id"], ["Property name"])
        command_map[item_id][GET_PROPERTY_TYPE] = CommandInfo("H", ["property_id"], ["Property type"])

        command_name_map[item_id]["reset_item"] = RESET_ITEM
        command_name_map[item_id]["get_item_name"] = GET_ITEM_NAME
        command_name_map[item_id]["get_item_type"] = GET_ITEM_TYPE
        command_name_map[item_id]["get_command_ids"] = GET_COMMAND_IDS
        command_name_map[item_id]["get_property_ids"] = GET_PROPERTY_IDS
        command_name_map[item_id]["get_command_name"] = GET_COMMAND_NAME
        command_name_map[item_id]["get_command_input_param_format"] = GET_COMMAND_INPUT_PARAM_FORMAT
        command_name_map[item_id]["get_command_input_param_names"] = GET_COMMAND_INPUT_PARAM_NAMES
        command_name_map[item_id]["get_command_input_param_names"] = GET_COMMAND_INPUT_PARAM_NAMES
        command_name_map[item_id]["get_command_output_param_desc"] = GET_COMMAND_OUTPUT_PARAM_DESC
        command_name_map[item_id]["get_property_name"] = GET_PROPERTY_NAME
        command_name_map[item_id]["get_property_type"] = GET_PROPERTY_TYPE
        return

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

        t1 = time.time()

        # If there is a deferred item, yield that first
        if self._attached_item_d is not None:
            yield self._attached_item_d

        # If we were already in the process of a discovery we should
        # return a deferred object.
        if self._in_progress:
            defer.returnValue(self._discovery_deferred)

        self._in_progress = True
        self.items = []
        for subsystem_id in self._subsystem_ids:
            try:
                yield self._get_item_discovery_info(subsystem_id)
            except Exception as e:
                print("Exception while discovering! Skipping subsystem : " + str(subsystem_id) + "\n    " + str(e))

        self._in_progress = False

        t2 = time.time()

        print "Discovery took", (t2 - t1), "seconds"
        # At this point self.items should be populated with
        # the ParlayStandardItem objects that represent the items we discovered.
        # By calling BaseProtocol's get_discovery() function we can get that information
        # to the adapter and furthermore to the broker.
        defer.returnValue(BaseProtocol.get_discovery(self))


    @staticmethod
    def command_cb(command_info_list, item_id, command_id, command_dropdowns, command_subfields, parlay_item, hidden=False):
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

        local_subfields = []

        c_name = command_info_list[0]
        c_input_format = command_info_list[1]
        c_input_names = command_info_list[2]
        c_output_desc = command_info_list[3]

        command_map[item_id][command_id] = CommandInfo(c_input_format, c_input_names,
                                                      c_output_desc)

        command_name_map[item_id][c_name] = command_id

        if not hidden:
            command_dropdowns.append((c_name, command_id))

            for position, parameter in enumerate(c_input_names):
                #TODO: add logic to obtain correct input type from format string
                local_subfields.append(parlay_item.create_field(msg_key=position, label=parameter, input=INPUT_TYPES.STRING, required=True))

            local_subfields.append(parlay_item.create_field(msg_key='__format__', input=INPUT_TYPES.STRING, hidden=True, default=c_input_format))
            command_subfields.append(local_subfields)

        return


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

        property_name = property_info_list[0]
        property_type = property_info_list[1]

        property_name_map[item_id][property_name] = property_id
        property_map[item_id][property_id] = PropertyData(name=property_name, format=property_type)

        parlay_item.add_property(property_id, name=property_name)
        parlay_item.add_datastream(property_id, name=property_name + "_stream")
        return

    @staticmethod
    def _initialize_reactor_command_map(reactor):
        command_map[reactor] = {}
        command_map[reactor][GET_ERROR_CODES] = CommandInfo("", [], ["codes"])
        command_map[reactor][GET_ERROR_STRING] = CommandInfo("H", ["code"], ["string"])

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
        print "Running discovery on subsystem: ", subsystem_id

        # Convert subsystem IDs to ints so that we can send them
        # back down the serial line to retrieve their attached item
        # For each subsystem ID, fetch the items attached to it

        print "Fetching items from subsystem ID: ", subsystem_id

        REACTOR = subsystem_id << self.SUBSYSTEM_SHIFT
        response = yield self.send_command(REACTOR, "DIRECT")
        self._item_ids = [int(item_id) for item_id in response.data]  # TODO: Change to extend() to get all item IDs


        # Fetch error codes
        self._initialize_reactor_command_map(REACTOR)
        response = yield self.send_command(to=REACTOR, tx_type="DIRECT", command_id=GET_ERROR_CODES)
        self._error_codes = [int(error_code) for error_code in response.data]

        for error_code in self._error_codes:
            response = yield self.send_command(to=REACTOR, tx_type="DIRECT", command_id=GET_ERROR_STRING, params=['0'], data=[error_code])
            error_code_string = response.data[0]
            error_code_map[error_code] = error_code_string

        print "---> ITEM IDS FOUND: ", self._item_ids

        for item_id in self._item_ids:
            self.adapter.subscribe(self.add_message_to_queue, TO=item_id)
            PCOMSerial.initialize_command_maps(item_id)

        for item_id in self._item_ids:
            response = yield self.send_command(item_id, command_id=GET_ITEM_NAME, tx_type="DIRECT")
            item_name = str(response.data[0])

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

            discovered_command = defer.DeferredList([])

            for command_id in command_ids:
                # Loop through the command IDs and build the Parlay Item object
                # for each one

                command_name = self.get_command_name(item_id, command_id)
                command_input_format = self.get_command_input_param_format(item_id, command_id)
                command_input_param_names = self.get_command_input_param_names(item_id, command_id)
                command_output_desc = self.get_command_output_parameter_desc(item_id, command_id)

                discovered_command = defer.gatherResults([command_name, command_input_format, command_input_param_names, command_output_desc])
                discovered_command.addCallback(PCOMSerial.command_cb, item_id=item_id, command_id=command_id,
                                               command_subfields=command_subfields, command_dropdowns=command_dropdowns,
                                               parlay_item=parlay_item, hidden=(command_id in DISCOVERY_MESSAGES))

            yield discovered_command

            response = yield self.send_command(item_id, command_id=GET_PROPERTY_IDS, tx_type="DIRECT")
            property_ids = response.data

            discovered_property = defer.DeferredList([])

            for property_id in property_ids:
                property_name = self.get_property_name(item_id, property_id)
                property_type = self.get_property_type(item_id, property_id)

                discovered_property = defer.gatherResults([property_name, property_type])
                discovered_property.addCallback(PCOMSerial.property_cb, item_id=item_id, property_id=property_id,
                                                parlay_item=parlay_item)

            yield discovered_property

            if item_type != ITEM_TYPE_HIDDEN:
                self.items.append(parlay_item)

            print "Finished ITEM:", item_name

        print "Finished subsystem:", subsystem
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
        # print "---> Message to be published: ", parlay_msg
        self.adapter.publish(parlay_msg, self.transport.write)

        # If we need to ack, ACK!
        if ack_expected:
            ack = str(p_wrap(ack_nak_message(sequence_num, True)))
            self.transport.write(ack)
            # print "---> ACK MESSAGE SENT"
            # print [hex(ord(x)) for x in ack]

        # also send it to discovery listener locally
        self._discovery_listener(msg)

    def lineReceived(self, line):
        """
        If this function is called we have received a <line> on the serial port
        that ended in 0x03.

        :param line:
        :return:
        """

        # print "--->Line received was called!"
        # print [hex(ord(x)) for x in line]

        # Using byte array so unstuff can use numbers instead of strings
        buf = bytearray()
        start_byte_index = (line.rfind(START_BYTE_STR) + 1)
        buf += line
        packet_tuple = unstuff_packet(buf[start_byte_index:])
        self._on_packet(*packet_tuple)


class CommandInfo:
    """
    CommandInfo is class used to store the information of the commands
    received from the embedded device.
    """

    def __init__(self, fmt, parameters, output_names):
        self.fmt = fmt
        self.params = parameters
        self.output_names = output_names


class ACKInfo:
    """
    Stores ACK information: deferred and number of retries
    """

    def __init__(self, sequence_number, num_retries, packet, transport):
        self.deferred = defer.Deferred()
        self.num_retries = num_retries
        self.sequence_number = sequence_number
        self.transport = transport
        self.packet = packet


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
                    print "UNEXPECTED ACK SEQ NUM:", sequence_number, "DROPPING"
                    return

                self._last_acked_map[sequence_number % self.WINDOW_SIZE] = sequence_number

            del self._window[sequence_number]
            if len(self._queue) > 0:
                ack_to_add = self._queue.pop(0)
                self.add_to_window(ack_to_add)

    def ack_timeout_errback(self, timeout_failure):
        """
        Errback that is called on ACK timeout
        :param timeout_exception: TimeoutException object that holds the ACK sequence number that timed out
        :return:
        """

        ack_to_send = self._window[timeout_failure.value.sequence_number]
        if ack_to_send.num_retries < self.NUM_RETRIES:
            print "TIMEOUT SEQ NUM", timeout_failure.value.sequence_number, " RESENDING..."
            ack_to_send.transport.write(ack_to_send.packet)
            ack_to_send.num_retries += 1
            d = defer.Deferred()
            d.addErrback(self.ack_timeout_errback)
            d.addCallback(self.ack_received_callback)
            ack_to_send.deferred = d
            self.ack_timeout(ack_to_send.deferred, PCOMSerial.ACK_TIMEOUT, ack_to_send.sequence_number)
            return self.TIMEOUT

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
        :param ack_info:
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





