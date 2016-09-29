"""

Serial_encoding.py

A collection of helper functions that aid in the packing and unpacking of binary data as part
of the PCOM Serial Protocol.


"""

import struct
import array
import sys
from enums import *
import pcom_message
import collections

import pcom_serial as pcom_serial

FORMAT_STRING_TABLE = {

    'B':   1,
    'b':   1,
    'x':   1,
    'c':   1,
    'H':   2,
    'h':   2,
    'I':   4,
    'i':   4,
    'Q':   8,
    'q':   8,
    'f':   4,
    'd':   8,
    's':   1,
    '?':   1

}


def p_wrap(stream):
    """
    Do the promenade wrap! The promenade protocol looks like:

    START BYTE <byte sequence> END BYTE

    Where START BYTE and END BYTE are 0x02 and 0x02 (for now at least).

    Since there is a possibility of 0x02 and 0x03 appearing in the data stream we must added 0x10 to all
    0x10, 0x02, 0x03 bytes and 0x10 should be inserted before each "problem" byte.

    For example

    stream = 0x03 0x04 0x05 0x06 0x07
    p_wrap(stream) = 0x02 0x10 0x13 0x04 0x05 0x06 0x07 0x03

    :param stream: A raw stream of bytes
    :return: A bytearray that has been run through the Promenade protocol
    """

    msg = bytearray()
    msg.append(START_BYTE)  # START
    for b in stream:
        if b in [START_BYTE, ESCAPE_BYTE, END_BYTE]:
            msg.append(ESCAPE_BYTE)
            msg.append(b + ESCAPE_BYTE)
        else:
            msg.append(b)
    msg.append(END_BYTE)
    return msg


def ack_nak_message(sequence_num, is_ack):
    """
    Generate an Ack message with the packets sequence number

    ------------------------------------------------------
    | ACK_MASK | Seq num, CHECKSUM, PAYLOAD SIZE (2 bytes)|
    ------------------------------------------------------
    """

    type_mask = TYPE_ACK if is_ack else TYPE_NAK
    return bytearray([sequence_num | type_mask, 0x100 - (sequence_num | type_mask), 0, 0])


def encode_pcom_message(msg):
    """
    Build the base binary message without the data sections
    :type msg: PCOMMessage


    Bytes [0:1]   Event ID (Unique ID of event)
    Bytes [2:3]   Source ID
    Bytes [4:5]   Destination ID
    Bytes [6:7]   Order/response code (Command ID, property ID, or status code depending on event type)
    Bytes [8:9]   Message Status
    Bytes [10]    Type (Type and subtype of event)
    Bytes [11]    Attributes (Event attributes)
    Bytes [12:N]  Format string (Null terminated data structure description (0 for no data))
    Bytes [N+1:M] Data in the form of bytes[10:N]. Size must match format string


    format_string: Describes the structure of the data using a character for each type.
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
                                | bool              |    ?          |    1      |
                                |-------------------|---------------|-----------|

    """

    payload = struct.pack("<HHHHHBB", msg.msg_id, msg.from_, msg.to, serialize_response_code(msg), msg.msg_status,
                          serialize_msg_type(msg), serialize_msg_attrs(msg))

    if msg.format_string:
        payload += struct.pack("%ds" % len(msg.format_string), msg.format_string)

    # NULL terminate the format_string,
    # or if there isn't a format_string we just
    # want a NULL byte
    payload += struct.pack("B", 0)

    # If there is data to send, msg.format_string should not
    # contain anything. We want to add msg.data and a NULL byte
    # to the payload.
    if msg.data:
        msg.data = cast_data(msg.format_string, msg.data)
        flattened_data = flatten(msg.data)
        payload += struct.pack("<" + translate_fmt_str(msg.format_string, msg.data), *flattened_data)

    return payload


def flatten(data):
    """
    Flattens an irregular data list into one list.

    Eg.

    [1, 2, [3, 4]] -> [1, 2, 3, 4]

     NOTE: used for handling variable length format string data

     FORMAT STRING = b*b
     DATA = [2, [3, 4]]

     Data needs to be packed as struct.pack('b2b', 2, 3, 4)
    :param data:  data list to be packed
    :return: flattened data lilst
    """
    if isinstance(data, collections.Iterable):
        return [element for lst in data for element in flatten(lst)]
    else:
        return [data]


def expand_fmt_string(format_string):
    """
    Expands the format string to use preexisting logic to cast the data
    accordingly.

    Example usage:

    expand_fmt_string("3H") --> "HHH"

    :param format_string: string of format chars from the format string table
    :return: format string that has been expanded from the condensed form.
    """

    multiplier = ''
    result = ''

    for i in format_string:
        if i.isdigit():
            multiplier += i
        else:
            multiplier = 1 if multiplier is '' else int(multiplier)
            result += multiplier * i
            multiplier = ''  # reset multiplier

    return result


def convert_to_bool(bool_obj):
    """
    Helper function to convert strings to boolean for casting purposes
    :param bool_string: boolean in string format, eg. "False" , or "True"
    :return: bool value
    """
    if type(bool_obj) in [bool, int]:
        return bool_obj
    return bool_obj.lower().strip() in ("yes", "true", "1")


def cast_data(fmt_string, data):
    """
    Returns a list of data casted according to the format string to prepare for
    packing.

    When using the struct library the data that is going to be packed must
    match the type of the format string character that it maps to.

    Eg.

    struct.pack("?", True)

    requires a boolean. The data received from the JSON message is a string, so
    we need to use this funtion to map it to the correct type.

    Example usage:

    cast_data("*B", ["12, 13, 14"]) --> [[12, 13, 14]]
    cast_data("Hs", ["12", "test"] --> [12, "test"]

    The return list will be sent to struct.pack()

    :param fmt_string: String of format characters from format string table
    :param data: List of strings representing the data that will be sent down
    the serial line
    :return: List of data that is now casted to the correct type according to
    the format string
    """

    result = []
    index = 0
    expanded_fmt = expand_fmt_string(fmt_string)
    skip_next = 0

    for fmt_index, i in enumerate(expanded_fmt):
        if skip_next:
            skip_next = 0
            continue

        if i == '*':
            variable_array = data[index].split(",") if isinstance(data[index], basestring) else data[index]
            new_fmt_str = str(len(variable_array)) + expanded_fmt[fmt_index+1]
            result.append(cast_data(expand_fmt_string(new_fmt_str), variable_array))
            skip_next = 1
        elif i.isalpha():
            if i in "bBhHiIlLqQnNx":  # TODO: Should padding (x) be int?
                data_to_append = int(data[index], 0) if isinstance(data[index], basestring) else int(data[index])
                result.append(data_to_append)
            elif i in "fd":
                result.append(float(data[index]))
            elif i in "s":
                result.append(str(data[index]))
            elif i in "c":
                result.append(str(data[index]).strip())
            else:
                raise Exception("Unhandled data type")
        elif i == '?':
            result.append(convert_to_bool(data[index]))
        else:
            raise Exception("Format string wasn't of type string")

        index += 1

    return result


def serialize_msg_attrs(msg):
    """
    :param msg: A Message object that was translated from a Parlay JSON message
    :return: A byte representing the attributes field of the byte sequence. This
    will be sent to the embedded core.
    """

    return msg.attributes


def serialize_response_code(message):
    """
    :param message: PCOM message object that we will be serializing
    :return:
    """

    VALID_MSG_TYPES = ["COMMAND", "EVENT", "RESPONSE", "PROPERTY", "STREAM"]

    m_type = message.msg_type

    if m_type in VALID_MSG_TYPES:
        code = message.contents.get("STATUS" if m_type == "RESPONSE" else m_type, None)
        if isinstance(code, basestring):
            map_to_use = pcom_serial.command_name_map if m_type == "COMMAND" else pcom_serial.property_name_map if m_type == "PROPERTY" else None
            if map_to_use:
                return pcom_message.PCOMMessage._look_up_id(map_to_use, message.to, code)
        elif type(code) == int:
            return code

    raise Exception("Response code could not be generated for message type: " + m_type)



def serialize_msg_type(msg):
    """
    Converts the message type to a binary sequence.
    :param msg: PCOM Message
    :return:
    """

    cat = get_category(msg)
    sub_type = get_sub_type(msg, cat)
    option = get_option(msg, cat, sub_type)

    serial_type = (cat << CATEGORY_SHIFT) | (sub_type << SUB_TYPE_SHIFT) | (option << OPTION_SHIFT)

    return serial_type


def get_category(message):
    """
    Gets the serial category of the PCOM Message..
    0 -- Order
    1 -- Order Response
    2 -- Notification

    :param message: PCOM Message that we want the category of
    :return: Category of the PCOM message, will be a number between 0-2.
    """
    m_type = message.msg_type

    if m_type in ORDER_TYPES:
        return MessageCategory.Order
    elif m_type in NOTIFICATION_TYPES:
        return MessageCategory.Order_Response
    elif m_type in RESPONSE_TYPES:
        return MessageCategory.Notification
    else:
        raise Exception('Unhandled message type!')


def get_sub_type(msg, category):
    """
    Extracts the sub type of the PCOM message passed as the parameter
    msg

    :param msg: PCOM message that we will be getting the sub type of
    :param category: category of the PCOM message that we want the subtype of
    :return: the subtype of the message (will be a number between 0-4)
    """

    m_type = msg.msg_type
    if category == MessageCategory.Order:

        if m_type == 'COMMAND':
            return OrderSubType.Command
        elif m_type == 'PROPERTY' or m_type == 'STREAM':
            return OrderSubType.Property
        else:
            raise Exception("Unsupported type")

    elif category == MessageCategory.Order_Response:
        if "RESULT" in msg.contents:
            return ResponseSubType.Command
        else:
            return ResponseSubType.Property

        # NOTE: Need to handle change state
    elif category == MessageCategory.Notification:
        return NotificationSubType.Info


def get_option(msg, cat, sub_type):
    """

    Returns the option portion of the message type.

    When serializing the message type (1 byte) the format follows:

    bytes[6:7] -- category
    bytes[4:5] -- sub type
    bytes[0:3] -- option

    Given a PCOM Message, and the message's sub type and category
    this function will return the corresponding option so that
    it may be serialized

    :param msg: PCOM Message we will be getting the option of
    :param cat: category of the PCOM message
    :param sub_type: sub type of the PCOM message
    :return: the option of the message (will be a number between 0-4)
    """

    if cat == MessageCategory.Order:
        if sub_type == OrderSubType.Command:
            return OrderCommandOption.Normal
        elif sub_type == OrderSubType.Property:
            action = msg.contents.get("ACTION", None)
            stop = msg.contents.get("STOP", None)
            if action == "GET":
                return OrderPropertyOption.Get_Property
            elif action == "SET":
                return OrderPropertyOption.Set_Property
            elif not stop:
                return OrderPropertyOption.Stream_On
            elif stop:
                return OrderPropertyOption.Stream_Off
            else:
                raise Exception("Unsupported option")

    elif cat == MessageCategory.OrderResponse:
        if sub_type == ResponseSubType.Command:
            # Change logic to handle inprogress commands
            return ResponseCommandOption.Complete
        elif sub_type == ResponseSubType.Property:
            if "VALUE" in msg.contents:
                return ResponsePropertyOption.Get_Response
            else:
                return ResponsePropertyOption.Set_Response

    elif cat == MessageCategory.Notification:
        raise Exception("Notifications aren't supported yet")

    else:

        raise Exception("Unhandled category")


def decode_pcom_message(binary_msg):
    """
    Build a pcom message object from the serialized message
    :type binary_msg: str
    :return : PCOMMessage
    """

    msg_length = len(binary_msg)

    # ensure the packet is big enough
    if msg_length < PACKET_HEADER_SIZE:
        raise Exception('binary message less than minimum size', binary_msg)

    msg = pcom_message.PCOMMessage()

    # Unpack the header

    msg.msg_id, msg.from_, msg.to, msg.response_code, msg.msg_status, msg.msg_type, msg.attributes \
        = struct.unpack("<HHHHHBB", binary_msg[0:PACKET_HEADER_SIZE])

    # Extract the format string
    format_string_end_index = str(binary_msg).find('\0', PACKET_HEADER_SIZE)
    format_string = binary_msg[PACKET_HEADER_SIZE:format_string_end_index+1]
    format_string_length = (format_string_end_index+1) - PACKET_HEADER_SIZE

    # If the format string only contains the NULL byte
    # our data and format_string should be empty
    if format_string_length == 1:
        msg.format_string = ''
        msg.data = []
        return msg

    # NOTE: There is a comma here because unpack returns a tuple
    msg.format_string, = struct.unpack("<%ds" % format_string_length, format_string)

    # The embedded serial message will have a format where string lengths are variable,
    # for example 'hsb'. The unpack() method requires string lengths to be specified, so we need
    # to translate the format string from the embedded message to something that unpack can understand like 'h12sb'
    receive_format = "<" + translate_fmt_str(msg.format_string, binary_msg[format_string_end_index+1:])

    # Strip NULL byte from format string
    msg.format_string = msg.format_string[:-1]

    # NOTE: Struct unpack returns a tuple, which will be cast to a list
    # through msg.data's property function.
    # Each string in the data list will have a NULL byte attached to it.
    msg.data = struct.unpack(receive_format, binary_msg[format_string_end_index+1:])

    # Remove null byte from all strings in data
    # print msg.data
    msg.data = map(lambda s: s[:-1] if isinstance(s, basestring) and s.endswith('\x00') else s, msg.data)

    # Map booleans to numbers until UI handles booleans correctly.
    msg.data = map(lambda s: int(s) if type(s) == bool else s, msg.data)

    # It's possible to receive empty strings for parameter requests.
    # In the case that we do receive an empty string we should not store it in data
    msg.data = filter(lambda x: x != '', msg.data)
    # print msg.data
    return msg


def get_str_len(bin_data):
    """
    Helper function that gets the length of a binary sequence up to
    and including the NULL byte.

    :param bin_data: binary byte sequence
    :return: count (including NULL byte) of bytes
    """

    count = 0
    for i in bin_data:
        if i is not '\x00':
            count += 1
        else:
            return count + 1  # Include NULL byte
    raise Exception("Data string wasn't NULL terminated")


def translate_fmt_str(fmt_str, data):
    """
    Given a format string used in the Embedded Core Protocol and a binary message
    returns a format string that may be used in the Python struct library.

    More specifically, the variable "s" format character is translated to
    "<str len>s".

    eg:

    translate_fmt_str("s", "\x65\x64\x00") --> "3s" because the length of the string is 3 (including NULL byte).

    :param fmt_str: A format string where 's' represents a variable length
    :param data: Binary sequence or list of bytes where strings are NULL terminated
    :return: a new format string where 's' is replaced by '<len>s' where len is the length
    of the string represented by 's'.
    """

    output_str = ""
    int_holder = ''
    index = 0
    is_binary = True

    if type(data) == list:
        is_binary = False

    for fmt_index, char in enumerate(fmt_str):

        if char == '*':
            if is_binary:
                rest_of_data_len = (len(data) - index)
                rest_of_data_len /= FORMAT_STRING_TABLE[fmt_str[fmt_index+1]]
                output_str += str(rest_of_data_len)
                index += rest_of_data_len
            else:
                output_str += str(len(data[index]))
                index += 1
            continue

        if char.isdigit():
            int_holder += char

        if char.isalpha() or char == '?':
            if char is 's':
                count = get_str_len(data[index:]) if is_binary else len(data[index])+1
                output_str += str(count)
            else:
                multiplier = 1 if len(int_holder) == 0 else int(int_holder)
                count = FORMAT_STRING_TABLE[char] * multiplier if is_binary else multiplier
                int_holder = ''

            index += count

        output_str += char

    return output_str


def pack_little_endian(type_string, data_list):
    """
    :param type_string:
    :param data_list:
    :return:
    """
    a = array.array(type_string, data_list)
    if sys.byteorder != 'little':
        a.byteswap()

    return a


# TESTING PURPOSES

def hex_print(buf):
    """
    :param buf:
    :return:
    """
    print [hex(ord(x)) for x in buf]


def wrap_packet(packet, sequence_num, use_ack):
    """
    Appends sequence number + packet type, checksum, and payload length to the payload.

    Also escapes packet according to the pcom protocol.

    :param packet: packet that will be wrapped
    :param sequence_num: current sequence number (nibble)
    :param use_ack: whether an ack is required or not
    :return: the resulting wrapped packet
    """

    # generate the packet header with the sequence number, length and ack
    # information

    NORMAL = 8
    ACK = 2

    payload_length = len(packet)
    sequence_byte = sequence_num | (NORMAL << 4) if use_ack else sequence_num | (ACK << 4)

    binary_msg = bytearray([sequence_byte])
    binary_msg += struct.pack("<H", payload_length & 0xFFFF)
    binary_msg += bytearray(packet)

    msg_sum = sum_packet(binary_msg)
    checksum = get_checksum(msg_sum)

    #verify checksum
    if (msg_sum + checksum) & 0xff != 0:
        raise ValueError("Checksum didn't equal zero!")

    binary_msg = bytearray([sequence_byte, checksum]) + binary_msg[1:]


    # Add the start stop and escape characters and send over serial port
    return p_wrap(binary_msg)


def unstuff_packet(packet):
    """
    Unstuff the packet. Descape and return sequence number, ack_expected, is_ack, and is_nak, dict_msg as a tuple
    dict_msg is the message (if there is one) or None (if it's an ack/nak)
    """

    packet = _deescape_packet(packet)
    packet_len = len(packet)

    if sum_packet(packet) != 0:
        print "WARNING PACKET DIDNT ADD UP TO ZERO"

    if packet_len < 1:
        raise IndexError("Packets must be AT LEAST 3 bytes long. packet was: " + str(packet))

    # Read the UART packet header information
    sequence_num = packet[0] & PACKET_SEQ_MASK
    packet_type = (packet[0] & PACKET_TYPE_MASK)

    ack_expected = (packet_type == TYPE_ACK_REQ)
    is_ack = (packet_type == TYPE_ACK)
    is_nak = (packet_type == TYPE_NAK)

    data = packet[4: packet_len]

    json_msg = None if (is_ack or is_nak) else decode_pcom_message(buffer(data))

    return sequence_num, ack_expected, is_ack, is_nak, json_msg

def _deescape_packet(packet):
    """
    :param packet:
    :return:
    """

    result = bytearray()
    escaped = False
    for b in packet:
        if b == ESCAPE_BYTE:
            escaped = True  # next byte is escaped
        elif escaped:
            result.append(b - ESCAPE_BYTE)  # remove escaped addition
            escaped = False
        else:
            result.append(b)

    return result


def sum_packet(msg):
    """
    Calculate the checksum for the given msg
    """

    checksum = 0
    for b in msg:
        checksum = (checksum + b)
    return checksum & 0xff

def get_checksum(packet_sum):
    """
    Calculates the checksum given the summation of the packet
    :param packet_sum: sum of all bytes in the packet
    :return: checksum for the packet
    """

    return -packet_sum & 0xff



