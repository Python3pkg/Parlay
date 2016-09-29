from parlay.enum import enum


INVALID_ID = 0xffff

# NOTE: These are global because the serial_encoding.py and
# pcom_message.py modules need access to them. Since they will
# be large maps we do not want to pass them as parameters.

GET_SUBSYSTEMS = 0
RESET_ITEM = 1000
GET_ITEM_NAME = 1001
GET_ITEM_TYPE = 1002
GET_COMMAND_IDS = 1003
GET_PROPERTY_IDS = 1004
GET_COMMAND_NAME = 1010
GET_COMMAND_INPUT_PARAM_FORMAT = 1011
GET_COMMAND_INPUT_PARAM_NAMES = 1012
GET_COMMAND_OUTPUT_PARAM_DESC = 1013
GET_PROPERTY_NAME = 1020
GET_PROPERTY_TYPE = 1021
GET_ERROR_CODES = 1
GET_ERROR_STRING = 2

DISCOVERY_MESSAGES = [GET_ITEM_NAME, GET_ITEM_TYPE, GET_COMMAND_IDS, GET_PROPERTY_IDS, GET_COMMAND_NAME,
                      GET_COMMAND_INPUT_PARAM_FORMAT, GET_COMMAND_INPUT_PARAM_NAMES, GET_COMMAND_OUTPUT_PARAM_DESC,
                      GET_PROPERTY_NAME, GET_PROPERTY_TYPE, GET_SUBSYSTEMS, RESET_ITEM]



ITEM_TYPE_HIDDEN = 1000
# From serial_encoding.py

START_BYTE = 0x02
END_BYTE = 0x03
ESCAPE_BYTE = 0x10

STATUS_SUCCESS = 0
STATUS_ERROR = 1

START_BYTE_STR = b'\x02'
END_BYTE_STR  = b'\x03'
ESCAPE_BYTE_STR = b'\x10'

PACKET_TYPE_MASK = 0xf0
PACKET_SEQ_MASK = 0x0f
PACKET_HEADER_SIZE = 12

TYPE_ACK = 0x20
TYPE_NAK = 0x30
TYPE_NO_ACK_REQ = 0x40
TYPE_ACK_REQ = 0x80

# From pcom_message.py

OPTION_MASK = 0x0f
OPTION_SHIFT = 0

SUB_TYPE_MASK = 0x30
SUB_TYPE_SHIFT = 4

CATEGORY_MASK = 0xc0
CATEGORY_SHIFT = 6


SUBSYSTEM_MASK = 0xff00
BROADCAST_ID = 0x80
SUBSYSTEM_SHIFT = 2

ORDER_TYPES = ["COMMAND", "PROPERTY", "STREAM"]
NOTIFICATION_TYPES = ["EVENT"]
RESPONSE_TYPES = ["RESPONSE"]
MessageCategory = enum(
    'Order',
    'Order_Response',
    'Notification'
)

OrderSubType = enum(
    'Command',
    'Property'
)

ResponseSubType = enum(
    'Command',
    'Property'
)

NotificationSubType = enum(
    'Info'
)

OrderCommandOption = enum(
    'Normal',
    'Special'
)

OrderPropertyOption = enum(
    'Get_Property',
    'Set_Property',
    'Stream_On',
    'Stream_Off'
)

ResponseCommandOption = enum(
    'Complete',
    'Inprogress'
)

ResponsePropertyOption = enum(
    'Get_Response',
    'Set_Response',
    'Stream_Response'
)

NotificationOptions = enum(
    'Error',
    'Warning',
    'Info',
    'Debug'
)

