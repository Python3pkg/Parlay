"""

PCOM_Message.py

This is a message class that represents a middle ground between the high level JSON message and low level serial message.

Variables in this class will serve as storage points for the information inside of each message. The variables
are accessed using properties (@property and @setter decorators).

There are two key functions in this class (from_json_msg() and to_json_msg()) which handle the
conversion to and from a JSON message.

"""

from parlay.protocols.utils import message_id_generator
import serial_encoding as serial_encoding
from parlay.protocols.pcom.enums import *
import pcom_serial as pcom_serial


class PCOMMessage(object):

    _item_lookup_map = {}

    # If we get a string ID , we need to assign a item ID. Start at 0xfc00 and go to 0xffff
    _item_id_generator = message_id_generator(0xffff, 0xfc00)

    VALID_JSON_MESSAGE_TYPES = ["COMMAND", "EVENT", "RESPONSE", "PROPERTY", "STREAM"]

    def __init__(self, to=None, from_=None, msg_id=0, tx_type=None, msg_type=None, attributes=0,
                 response_code=None, response_req=None, msg_status=None, contents=None, data=None, data_fmt=None):

        # TODO: Change response_req to response_code

        # private variables only accessed through @property functions

        self._msg_type = None
        self._to = None
        self._from_ = None
        self._tx_type = None
        self._response_req = None
        self._msg_status = None
        self._contents = None
        self._attributes = None
        self._format_string = ''
        self._data = []
        self._response_code = None

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
            item_id = cls._item_id_generator.next()
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
        """
        Given a command name or property name map, looks up the ID for the requested name
        for <destination_id> ID
        :param map:
        :param destination_id:
        :param command:
        :return:
        """
        if isinstance(name, basestring):
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
            if msg.to in pcom_serial.command_map:
                # command will be a CommandInfo object that has a list of parameters and format string
                command_id = msg.contents.get("COMMAND", INVALID_ID)
                command_int_id = cls._look_up_id(pcom_serial.command_name_map, msg.to, command_id)
                if command_int_id is None:
                    print "Could not find integer command ID for command name:", command_id
                    return
                command = pcom_serial.command_map[msg.to][command_int_id]
                fmt = str(msg.contents.get('__format__', command.fmt))
                for param_position in range(len(command.params)):
                    # TODO: May need to change default value to error out
                    data.append(msg.contents.get(str(param_position), 0))

        elif msg.msg_type == "PROPERTY":
            # If the message type is a "PROPERTY" there should be
            # a "PROPERTY" entry in the "CONTENTS" that has the property ID

            action = msg.contents.get('ACTION', None)

            if action == "GET":
                data = []
                fmt = ''
            elif action == "SET":
                if msg.to in pcom_serial.property_map:
                    property_id = msg.contents.get("PROPERTY", INVALID_ID)
                    property = cls._look_up_id(pcom_serial.property_name_map, msg.to, property_id)
                    if property is None:
                        print "Could not find integer property ID for property name:", property
                        return
                    prop = pcom_serial.property_map[msg.to][property]
                    fmt = prop.format
                    data.append(msg.contents['VALUE'] if msg.contents['VALUE'] is not None else 0)
                    data = serial_encoding.cast_data(fmt, data)

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

        msg = cls(to=to, from_=from_, msg_id=msg_id, response_req=response_req, msg_type=msg_type,
                  msg_status=msg_status, tx_type=tx_type, contents=contents)

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

    def get_tx_type_from_id(self, id):
        """
        Given an ID, returns the msg['TOPICS']['TX_TYPE'] that should be assigned
        :param id: destination item ID
        :return:
        """
        subsystem_id = self.get_subsystem(id)
        return "BROADCAST" if subsystem_id == BROADCAST_ID else "DIRECT"

    def to_json_msg(self):
        """
        :return:
        """

        msg = {'TOPICS': {}, 'CONTENTS': {}}
        msg['TOPICS']['TO'] = self._get_name_from_id(self.to)
        msg['TOPICS']['FROM'] = self._get_name_from_id(self.from_)
        msg['TOPICS']['MSG_ID'] = self.msg_id

        msg['TOPICS']['TX_TYPE'] = self.get_tx_type_from_id(self.to)

        if self.msg_status != STATUS_SUCCESS:
            msg['TOPICS']['MSG_TYPE'] = "RESPONSE"
            msg['CONTENTS']['STATUS'] = self.msg_status
            msg['TOPICS']['MSG_STATUS'] = "ERROR"
            msg['CONTENTS']['DESCRIPTION'] = pcom_serial.error_code_map.get(self.msg_status, "")
            msg['TOPICS']['RESPONSE_REQ'] = False
            return msg

        msg_category = self.category()
        msg_sub_type = self.sub_type()
        msg_option = self.option()

        msg['TOPICS']['RESPONSE_REQ'] = self._is_response_req()

        if msg_category == MessageCategory.Order:
            if msg_sub_type == OrderSubType.Command:
                if msg_option == OrderCommandOption.Normal:
                    msg['TOPICS']['MSG_TYPE'] = "COMMAND"
                    msg['CONTENTS']['COMMAND'] = self.response_code
            elif msg_sub_type == OrderSubType.Property:
                if msg_option == OrderPropertyOption.Get_Property:
                    msg['TOPICS']['MSG_TYPE'] = "PROPERTY"
                    msg['CONTENTS']['PROPERTY'] = self.response_code
                    msg['CONTENTS']['ACTION'] = "GET"
                elif msg_option == OrderPropertyOption.Set_Property:
                    msg['TOPICS']['MSG_TYPE'] = "PROPERTY"
                    msg['CONTENTS']['PROPERTY'] = self.response_code
                    msg['CONTENTS']['ACTION'] = "SET"
                    msg['CONTENTS']['VALUE'] = self.data[0] # TODO: Support no data
                elif msg_option == OrderPropertyOption.Stream_On:
                    raise Exception("Stream on not handled yet")
                elif msg_option == OrderPropertyOption.Stream_Off:
                    raise Exception("Stream off not handled yet")

            else:
                raise Exception("Unhandled message subtype {}", msg_sub_type)

        elif msg_category == MessageCategory.Order_Response:
            msg['TOPICS']['MSG_TYPE'] = "RESPONSE"
            msg['CONTENTS']['ERROR_CODE'] = self.msg_status
            if msg_sub_type == ResponseSubType.Command:
                item = pcom_serial.command_map.get(self.from_, None)

                if item:
                    if msg_option == ResponseCommandOption.Complete:
                        msg['TOPICS']['MSG_STATUS'] = "OK"
                    elif msg_option == ResponseCommandOption.Inprogress:
                        msg['TOPICS']['MSG_STATUS'] = "PROGRESS"
                    msg['CONTENTS']['RESULT'] = self._get_result_string(item[self.response_code].output_names)
                else:
                    msg['TOPICS']['MSG_STATUS'] = "ERROR"
                    msg['CONTENTS']['RESULT'] = {}

            elif msg_sub_type == ResponseSubType.Property:
                msg['TOPICS']['MSG_STATUS'] = "OK"
                if msg_option == ResponsePropertyOption.Get_Response:
                    msg['CONTENTS']['ACTION'] = "RESPONSE"
                    msg['CONTENTS']['PROPERTY'] = self.response_code
                    msg['CONTENTS']['VALUE'] = self.data[0] # TODO: support empty data
                elif msg_option == ResponsePropertyOption.Set_Response:
                    msg['CONTENTS']['ACTION'] = "RESPONSE"
                    msg['CONTENTS']['PROPERTY'] = self.response_code
                    pass # NOTE: set responses do not have a 'value' field
                elif msg_option == ResponsePropertyOption.Stream_Response:
                    msg['TOPICS']['MSG_TYPE'] = "STREAM"
                    msg['TOPICS']['STREAM'] = self.response_code
                    msg['CONTENTS']['VALUE'] = self.data[0]
                    msg['CONTENTS']['RATE'] = 1000  # Rate not obtained during discovery, using 1000 (ms) here as arbitrary value

        elif msg_category == MessageCategory.Notification:
            msg['TOPICS']["MSG_TYPE"] = "EVENT"
            msg['CONTENTS']['EVENT'] = self.response_code
            msg['CONTENTS']['STATUS'] = self.msg_status
            msg['CONTENTS']["INFO"] = self.data
            msg['CONTENTS']['DESCRIPTION'] = pcom_serial.error_code_map.get(self.msg_status, "")
            msg['TOPICS']['RESPONSE_REQ'] = False

            if msg_option == NotificationOptions.Debug:
                msg['TOPICS']['MSG_STATUS'] = "INFO"
            elif msg_option == NotificationOptions.Warning:
                msg['TOPICS']['MSG_STATUS'] = "WARNING"

        return msg

    def _get_result_string(self, output_param_names):
        """
        Given the output names of a command and a data list, returns a dictionary of output_names -> data

        :param output_param_names: The output names of a command found during discovery
        :param data_list: The data passed to the protocol from the command
        :return: a dictionary of output names mapped to their data segments
        """

        # If the first output parameter is a list then simply return
        # a all of the data
        if len(output_param_names) > 0 and  output_param_names[0][-2:] == "[]":
            return {output_param_names[0]: self.data}
        # Otherwise return a map of output names -> data
        else:
            return dict(zip(output_param_names, self.data))

    def category(self):
        return (self.msg_type & CATEGORY_MASK) >> CATEGORY_SHIFT

    def sub_type(self):
        return (self.msg_type & SUB_TYPE_MASK) >> SUB_TYPE_SHIFT

    def option(self):
        return (self.msg_type & OPTION_MASK) >> OPTION_SHIFT

    @property
    def to(self):
        return self._to

    @to.setter
    def to(self, value):
        self._to = value

    @property
    def from_(self):
        return self._from_

    @from_.setter
    def from_(self, value):
        self._from_ = value

    @property
    def msg_status(self):
        return self._msg_status

    @msg_status.setter
    def msg_status(self, value):
        self._msg_status = value

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, value):
        if hasattr(value, '__iter__'):
            self._data = list(value)
        elif value == None:
            self._data = []
        else:
            self._data = [value]

    @property
    def format_string(self):
        return self._format_string

    @format_string.setter
    def format_string(self, value):
        self._format_string = value

    @property
    def msg_type(self):
        return self._msg_type


    @msg_type.setter
    def msg_type(self, value):
        self._msg_type = value

    @property
    def command(self):
        return self._command

    @command.setter
    def command(self, value):
        self._event = None
        self._status = None
        self._command = value

    @property
    def response_code(self):
        return self._response_code

    @response_code.setter
    def response_code(self, value):
        self._response_code = value

    @property
    def event(self):
        return self._event

    @event.setter
    def event(self, value):
        self._event = value
        self._status = None
        self._command = None

    @property
    def attributes(self):
        return self._attributes

    @attributes.setter
    def attributes(self, value):
        self._attributes = value
        if value is not None:
            self.priority = value & 0x01

    @property
    def msg_status(self):
        return self._msg_status

    @msg_status.setter
    def msg_status(self, value):
        self._msg_status = value
