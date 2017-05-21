"""

PCOM_Message.py

This is a message class that represents a middle ground between the high level JSON message and low level serial message.

Variables in this class will serve as storage points for the information inside of each message. The variables
are accessed using properties (@property and @setter decorators).

There are two key functions in this class (from_json_msg() and to_json_msg()) which handle the
conversion to and from a JSON message.

"""

from parlay.protocols.utils import message_id_generator

from . import pcom_serial
import logging
from . import serial_encoding
from .enums import *

logger = logging.getLogger(__name__)


class PCOMMessage(object):

    _item_lookup_map = {}

    # If we get a string ID , we need to assign a item ID. Start at 0xfc00 and go to 0xffff
    _item_id_generator = message_id_generator(0xffff, 0xfc00)

    VALID_JSON_MESSAGE_TYPES = ["COMMAND", "EVENT", "RESPONSE", "PROPERTY", "STREAM"]

    GLOBAL_ERROR_CODE_ID = 0xff

    def __init__(self, to=None, from_=None, msg_id=0, tx_type=None, msg_type=None, attributes=0,
                 response_code=None, response_req=None, msg_status=None, contents=None, data=None, data_fmt=None, topics=None, description=''):

        # TODO: Change response_req to response_code

        # private variables only accessed through @property functions
        self._attributes = None
        self._format_string = ''
        self._data = []

        self.description = description

        self.to = to
        self.from_ = from_
        self.msg_id = msg_id
        self.tx_type = tx_type
        self.msg_type = msg_type
        self.response_req = response_req
        self.msg_status = msg_status
        self.contents = contents
        self.priority = 0
        self.attributes = attributes
        self.format_string = data_fmt
        self.data = data
        self.response_code = response_code
        self.topics = topics

    @classmethod
    def _get_item_id(cls, name):
        """
        Gets a item ID from an item name
        """

        # if we're an int we're good
        if type(name) == int:
            return name

        if name in cls._item_lookup_map:
            return cls._item_lookup_map[name]

        else:
            # if the item ID wasn't in our map, generate an int for it
            # and add it to the map
            item_id = next(cls._item_id_generator)
            cls._item_lookup_map[name] = item_id
            cls._item_lookup_map[item_id] = name
            return item_id

    @classmethod
    def _get_name_from_id(cls, item_id):
        """
        Gets a item name from an item ID
        """

        if item_id in cls._item_lookup_map:
            return cls._item_lookup_map[item_id]

        return item_id

    @staticmethod
    def _look_up_id(map, destination_id, name):
        if isinstance(name, str):
            # TODO: use .get() to avoid key error
            return map[destination_id].get(name, None)
        else:
            return name

    @classmethod
    def _get_data_format(cls, msg):
        """
        Takes a msg and does the appropriate table lookup to obtain the
        format data for the command/property/stream.

        Returns a tuple in the form of (data, format)
        where data is a list and format is a format string.

        :param msg:
        :return:
        """

        data = []
        fmt = ''
        if msg.msg_type == "COMMAND":
            # If the message type is "COMMAND" there should be an
            # entry in the 'CONTENTS' table for the command ID
            if msg.to in pcom_serial.PCOM_COMMAND_MAP:
                # command will be a CommandInfo object that has a list of parameters and format string
                command_id = msg.contents.get("COMMAND", INVALID_ID)
                command_int_id = cls._look_up_id(pcom_serial.PCOM_COMMAND_NAME_MAP, msg.to, command_id)
                if command_int_id is None:
                    logger.error("Could not find integer command ID for command name: {0}".format(command_id))
                    return
                # TODO: check for KeyError
                command = pcom_serial.PCOM_COMMAND_MAP[msg.to].get(command_int_id, None)
                if command is None:
                    return data, fmt

                fmt = str(msg.contents.get('__format__', command["format"]))
                for param in command["input params"]:
                    # TODO: May need to change default value to error out
                    data.append(msg.contents.get(str(param), 0))

        elif msg.msg_type == "PROPERTY":
            # If the message type is a "PROPERTY" there should be
            # a "PROPERTY" entry in the "CONTENTS" that has the property ID

            action = msg.contents.get('ACTION', None)

            if action == "GET":
                data = []
                fmt = ''
            elif action == "SET":
                if msg.to in pcom_serial.PCOM_PROPERTY_MAP:
                    property_id = msg.contents.get("PROPERTY", INVALID_ID)
                    property = cls._look_up_id(pcom_serial.PCOM_PROPERTY_NAME_MAP, msg.to, property_id)
                    if property is None:
                        logger.error("Could not find integer property ID for property name: {0}".format(property))
                        return
                    prop = pcom_serial.PCOM_PROPERTY_MAP[msg.to][property]
                    fmt = prop["format"]
                    content_data = msg.contents.get('VALUE', 0)
                    if type(content_data) == list:  # we have a variable property list
                        data = content_data
                    else:
                        data.append(content_data)
                    data = serial_encoding.cast_data(fmt, data)

        elif msg.msg_type == "STREAM":
            # no data or format string for stream messages
            rate = msg.contents.get("RATE", None)  # default rate = 1 Hz
            data = [rate] if rate else []
            fmt = 'f' if rate else ''

        return data, fmt

    @classmethod
    def from_json_msg(cls, json_msg):
        """
        Converts a dictionary message to a PCOM message object

        :param json_msg: JSON message
        :return: PCOM message object
        """

        msg_id = json_msg['TOPICS']['MSG_ID']

        to = cls._get_item_id(json_msg['TOPICS']['TO'])
        from_ = cls._get_item_id(json_msg['TOPICS']['FROM'])

        msg_type = json_msg['TOPICS']['MSG_TYPE']

        response_req = json_msg['TOPICS'].get("RESPONSE_REQ", False)

        msg_status = 0  # TODO: FIX THIS
        tx_type = json_msg['TOPICS'].get('TX_TYPE', "DIRECT")

        contents = json_msg['CONTENTS']
        topics = json_msg['TOPICS']

        msg = cls(to=to, from_=from_, msg_id=msg_id, response_req=response_req, msg_type=msg_type,
                  msg_status=msg_status, tx_type=tx_type, contents=contents, topics=topics)

        # Set data and format using class function
        msg.data, msg.format_string = cls._get_data_format(msg)
        return msg

    def _is_response_req(self):
        """
        If the msg is an order a response is expected.
        :return:
        """

        return (self.category()) == MessageCategory.Order

    @staticmethod
    def get_subsystem(id):
        """"
        Gets the subsystem of the message.
        """
        return (id & SUBSYSTEM_MASK) >> SUBSYSTEM_SHIFT

    def _get_data(self):
        """
        Helper function for returning the data of the PCOM Message. Returns an error message if there
        wasn't any data to get.
        :param index:
        :return:
        """
        if len(self.data) == 1:
            return self.data[0]
        if len(self.data) > 1:
            return self.data
        return None

    def get_tx_type_from_id(self, id):
        """
        Given an ID, returns the msg['TOPICS']['TX_TYPE'] that should be assigned
        :param id: destination item ID
        :return:
        """
        subsystem_id = self.get_subsystem(id)
        return "BROADCAST" if subsystem_id == BROADCAST_ID else "DIRECT"

    @staticmethod
    def get_name_from_id(item_id, map, id_to_find, default_val="No name found"):
        """
        Gets name from item ID. Assuming name is the KEY and ID is the value in <map> dictionary

        :param item_id:
        :param map:
        :param default_val:
        :return:
        """

        item_name_map = map.get(item_id, None)

        if not item_name_map:
            return default_val

        for name in item_name_map:
            if item_name_map[name] == id_to_find:
                return name

        return default_val

    def _build_parlay_command_message(self, msg):
        """
        Adds fields to Parlay message <msg> in accordance with command spec (3/15/2017).

        Command messages have:
        1) TOPICS -> MSG_TYPE -> COMMAND
        2) CONTENTS -> COMMAND -> COMMAND ID
        3) CONTENTS -> COMMAND NAME (optional)

        :param msg: Parlay message to be modified
        :return: None
        """

        destination_integer_id = self.to
        msg['TOPICS']['MSG_TYPE'] = "COMMAND"
        msg['CONTENTS']['COMMAND'] = self.response_code
        msg['CONTENTS']['COMMAND_NAME'] = self.get_name_from_id(destination_integer_id,
                                                                pcom_serial.PCOM_COMMAND_NAME_MAP,
                                                                self.response_code)

    def _build_parlay_property_get_msg(self, msg):
        """
        Adds fields to Parlay message <msg> in accordance with property GET spec (3/15/2017)

        Property GET messages have:
        1) TOPICS -> MSG_TYPE -> PROPERTY
        2) CONTENTS -> PROPERTY -> PROPERTY ID
        3) CONTENTS -> ACTION -> GET

        :param msg: Parlay message that will be modified
        :return: None
        """

        msg['TOPICS']['MSG_TYPE'] = "PROPERTY"
        msg['CONTENTS']['PROPERTY'] = self.response_code
        msg['CONTENTS']['ACTION'] = "GET"

    def _build_parlay_property_set_msg(self, msg):
        """
        Adds fields to Parlay message <msg> in accordance with property SET spec (3/15/2017)

        Property SET messages have:
        1) TOPICS -> MSG_TYPE -> PROPERTY
        2) CONTENTS -> PROPERTY -> PROPERTY ID
        3) CONTENTS -> ACTION -> SET
        4) CONTENTS -> VALUE -> PROPERTY VALUE

        :param msg: Parlay message (dictionary) that will be modified.
        :return: None
        """

        msg['TOPICS']['MSG_TYPE'] = "PROPERTY"
        msg['CONTENTS']['PROPERTY'] = self.response_code
        msg['CONTENTS']['ACTION'] = "SET"
        msg['CONTENTS']['VALUE'] = self._get_data()

    def _build_parlay_error_response_msg(self, msg):
        """
        Adds fields to Parlay message <msg> in accordance with Parlay error response spec. (3/15/2017)

        Parlay error responses have:
        1) CONTENTS -> ERROR_CODE -> ERROR STATUS # (from embedded board)
        2) CONTENTS -> DESCRIPTION -> ERROR DESCRIPTION
        3) TOPICS -> MSG_STATUS -> ERROR
        4) TOPICS -> RESPONSE_REQ -> False

        :param msg: Parlay message (dictionary) that will be modified.
        :return: None
        """

        def _get_id_name_from_error_code(error_code):
            if error_code >> pcom_serial.PCOMSerial.SUBSYSTEM_SHIFT == self.GLOBAL_ERROR_CODE_ID:
                return self.from_
            return (self.from_ & pcom_serial.PCOMSerial.SUBSYSTEM_ID_MASK) | (error_code >> pcom_serial.PCOMSerial.SUBSYSTEM_SHIFT)

        def _get_specific_code(error_code):
            if error_code >> pcom_serial.PCOMSerial.SUBSYSTEM_SHIFT != self.GLOBAL_ERROR_CODE_ID:
                return error_code & pcom_serial.PCOMSerial.ITEM_ID_MASK
            return error_code

        msg['CONTENTS']['ERROR_CODE'] = self.msg_status
        msg['TOPICS']['MSG_STATUS'] = "ERROR"
        msg['CONTENTS']['DESCRIPTION'] = pcom_serial.PCOM_ERROR_CODE_MAP.get(self.msg_status, self.description)
        msg['TOPICS']['RESPONSE_REQ'] = False
        msg['CONTENTS']['ERROR ORIGIN'] = pcom_serial.PCOM_ITEM_NAME_MAP.get(_get_id_name_from_error_code(self.msg_status), "REACTOR")
        msg['CONTENTS']['ITEM SPECIFIC ERROR CODE'] = _get_specific_code(self.msg_status)

    def _build_parlay_command_response(self, msg):
        """
        Adds fields to Parlay message <msg> in accordance with Parlay command response spec. (3/15/2017)

        Parlay command responses have:
        1) TOPICS -> MSG_STATUS -> PROGRESS/OK
        2) CONTENTS -> DATA depending on result

        :param msg: Parlay message (dictionary) that will be modified.
        :return: None
        """
        msg_option = self.option()
        item = pcom_serial.PCOM_COMMAND_MAP.get(self.from_, None)

        if item or self.response_code == 0:
            if msg_option == ResponseCommandOption.Complete:
                msg['TOPICS']['MSG_STATUS'] = "OK"
            elif msg_option == ResponseCommandOption.Inprogress:
                msg['TOPICS']['MSG_STATUS'] = "PROGRESS"

        else:
            msg["TOPICS"]["MSG_STATUS"] = "ERROR"
            msg["CONTENTS"]["DESCRIPTION"] = "PCOM ERROR: Could not find item:", self.from_

        if self.response_code == 0:
            self._build_contents_map(["Subsystems"], msg["CONTENTS"])
            return

        cmd = item.get(self.response_code, pcom_serial.PCOMSerial.build_command_info("", [], []))
        self._build_contents_map(cmd["output params"], msg["CONTENTS"])

    def _build_parlay_property_response(self, msg):
        """
        Adds fields to Parlay message <msg> in accordance with Parlay property response spec. (3/15/2017)

        :param msg: Parlay message (dictionary) that will be modified.
        :return: None
        """
        sender_integer_id = self.from_
        msg_option = self.option()

        msg['TOPICS']['MSG_STATUS'] = "OK"
        if msg_option == ResponsePropertyOption.Get_Response:
            msg['CONTENTS']['ACTION'] = "RESPONSE"
            id = self.response_code

            msg['CONTENTS']['PROPERTY'] = self.response_code
            msg['CONTENTS']['VALUE'] = self._get_data()
        elif msg_option == ResponsePropertyOption.Set_Response:
            msg['CONTENTS']['ACTION'] = "RESPONSE"
            msg['CONTENTS']['PROPERTY'] = self.response_code
            pass  # NOTE: set responses do not have a 'value' field
        elif msg_option == ResponsePropertyOption.Stream_Response:
            msg['TOPICS']['MSG_TYPE'] = "STREAM"
            id = self.response_code
            if type(id) == int:
                # convert to stream name ID
                id = self.get_name_from_id(sender_integer_id, pcom_serial.PCOM_STREAM_NAME_MAP, self.response_code,
                                           default_val=self.response_code)
            msg['TOPICS']['STREAM'] = id
            msg['CONTENTS']['STREAM'] = id
            msg['CONTENTS']['VALUE'] = self._get_data()

    def _build_parlay_notification(self, msg):
        """
        Adds fields to Parlay message <msg> in accordance with Parlay notification spec. (3/15/2017)

        :param msg: Parlay message (dictionary) that will be modified.
        :return: None
        """

        msg['TOPICS']["MSG_TYPE"] = "EVENT"
        msg['CONTENTS']['EVENT'] = self.response_code
        msg['CONTENTS']['ERROR_CODE'] = self.msg_status
        msg['CONTENTS']["INFO"] = self.data
        msg['CONTENTS']['DESCRIPTION'] = pcom_serial.PCOM_ERROR_CODE_MAP.get(self.msg_status, self.description)
        msg['TOPICS']['RESPONSE_REQ'] = False

    def _build_broadcast(self, msg):
        """
        Changes message to type BROADCAST.

        :param msg: Parlay message (dictionary) that will be modified.
        :return: None
        """

        msg_option = self.option()
        if msg_option == BroadcastNotificationOptions.External:
            msg['TOPICS']['TX_TYPE'] = "BROADCAST"
            if "TO" in msg['TOPICS']:
                del msg['TOPICS']['TO']
        else:
            raise Exception("Received internal broadcast message")

    def _add_notification_msg_status(self, msg):
        """
        Adds status fields to Parlay message <msg>

        :param msg: Parlay message (dictionary) that will be modified.
        :return: None
        """

        if self.msg_status == 0:
            msg['TOPICS']['MSG_STATUS'] = "INFO"
        elif self.msg_status > 0:
            msg['TOPICS']['MSG_STATUS'] = "ERROR"
        else:
            msg['TOPICS']['MSG_STATUS'] = "WARNING"

    def _build_parlay_stream_msg(self, msg, is_on):
        """
        Adds fields to Parlay message <msg> for stream commands.
        NOTE: The stream ID is put in contents and topics because some Parlay implementations check for it in
        TOPICS and some check for it in CONTENTS.

        :param msg: Parlay dictionary message that will be modified.
        :param is_on: Whether the stream command is turning the stream on or off.
        :return:
        """

        sender_integer_id = self.from_

        msg["TOPICS"]["MSG_TYPE"] = "STREAM"
        if type(id) == int:
            # convert to stream name ID
            id = self.get_name_from_id(sender_integer_id, pcom_serial.PCOM_STREAM_NAME_MAP, self.response_code,
                                       default_val=self.response_code)

        msg["TOPICS"]["STREAM"] = id
        msg["CONTENTS"]["STREAM"] = id
        msg["CONTENTS"]["STOP"] = is_on
        # TODO: Find out from Frances what the data looks like for stream commands

    def to_json_msg(self):
        """
        Converts from PCOMMessage type to Parlay JSON message. Returns message when translation is complete.
        :return: Parlay JSON message equivalent of this object
        """

        # Initialize our potential JSON message
        msg = {'TOPICS': {}, 'CONTENTS': {}}

        msg['TOPICS']['TO'] = self._get_name_from_id(self.to)
        msg['TOPICS']['FROM'] = self._get_name_from_id(self.from_)
        msg['TOPICS']['FROM_NAME'] = pcom_serial.PCOM_ITEM_NAME_MAP.get(self.from_, "")
        msg['TOPICS']['MSG_ID'] = self.msg_id

        # Default the message transmission type to "DIRECt".
        # This may be changed later if the message is a broadcast notification.
        msg['TOPICS']['TX_TYPE'] = "DIRECT"

        # Retrieve our message components
        msg_category = self.category()
        msg_sub_type = self.sub_type()
        msg_option = self.option()

        msg['TOPICS']['RESPONSE_REQ'] = self._is_response_req()

        # Handle Parlay command messages
        if msg_category == MessageCategory.Order:
            if msg_sub_type == OrderSubType.Command:
                if msg_option == OrderCommandOption.Normal:
                    self._build_parlay_command_message(msg)

            elif msg_sub_type == OrderSubType.Property:
                if msg_option == OrderPropertyOption.Get_Property:
                    self._build_parlay_property_get_msg(msg)

                elif msg_option == OrderPropertyOption.Set_Property:
                    self._build_parlay_property_set_msg(msg)

                elif msg_option == OrderPropertyOption.Stream_On:
                    self._build_parlay_stream_msg(msg, is_on=True)
                elif msg_option == OrderPropertyOption.Stream_Off:
                    self._build_parlay_stream_msg(msg, is_on=False)

            else:
                raise Exception("Unhandled message subtype {}", msg_sub_type)

        # Handle Parlay responses
        elif msg_category == MessageCategory.Order_Response:
            msg['TOPICS']['MSG_TYPE'] = "RESPONSE"

            if self.msg_status != STATUS_SUCCESS:
                self._build_parlay_error_response_msg(msg)
                return msg

            if msg_sub_type == ResponseSubType.Command:
                self._build_parlay_command_response(msg)

            elif msg_sub_type == ResponseSubType.Property:
                self._build_parlay_property_response(msg)

        # Handle Parlay notifications
        elif msg_category == MessageCategory.Notification:
            self._build_parlay_notification(msg)

            if msg_sub_type == NotificationSubType.Broadcast:
                self._build_broadcast(msg)

            elif msg_option == NotificationSubType.Direct:
                msg['TOPICS']['TX_TYPE'] = "DIRECT"

            else:
                raise Exception("Unhandled notification type")

            self._add_notification_msg_status(msg)

        return msg

    def _build_contents_map(self, output_param_names, contents_map):
        """
        Given the output names of a command and a data list, modifies <contents_map> accordingly.

        :param output_param_names: The output names of a command found during discovery.
        :param contents_map: The map that will be produced as part of a Parlay JSON message.
        :return: None
        """

        # If we don't have any data we do not need add anything to the contents
        # dictionary.
        if len(self.data) == 0:
            return

        # If there is only one output parameter we need to add a "RESULT" key to the
        # contents dictionary and fill it with our data as per Parlay spec 3/15/2017.
        if len(output_param_names) == 1:
            contents_map["RESULT"] = self.data[0] if len(self.data) == 1 else self.data
            return

        # If the number of output parameters does not match the number of data pieces
        # we can not reliably add { output_name -> data } pairs to the CONTENTS dictionary
        # so we should report an error and not add anything to CONTENTS.
        if len(output_param_names) != len(self.data):
            logger.error("[PCOM] ERROR: Could not produce contents dictionary for data: {0} and"
                         " output parameters: {1}".format(self.data, output_param_names))
            return

        # If we got here we know that the number of output parameters is the same
        # as the number of data pieces so we can match our output parameters to data pieces
        # 1:1. Simply zip them together and throw them in the CONTENTS dictionary.
        contents_map.update(dict(list(zip(output_param_names, self.data))))

    def category(self):
        return (self.msg_type & CATEGORY_MASK) >> CATEGORY_SHIFT

    def sub_type(self):
        return (self.msg_type & SUB_TYPE_MASK) >> SUB_TYPE_SHIFT

    def option(self):
        return (self.msg_type & OPTION_MASK) >> OPTION_SHIFT

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, value):
        if hasattr(value, '__iter__'):
            self._data = list(value)
        elif value is None:
            self._data = []
        else:
            self._data = [value]

    @property
    def attributes(self):
        return self._attributes

    @attributes.setter
    def attributes(self, value):
        self._attributes = value
        if value is not None:
            self.priority = value & 0x01
