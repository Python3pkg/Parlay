from twisted.internet import defer
from twisted.trial import unittest

from parlay.protocols.pcom.pcom_message import PCOMMessage
import parlay.protocols.pcom.enums as enums
import parlay.protocols.pcom.serial_encoding as serial_encoding
import parlay.protocols.pcom.pcom_serial as pcom_serial


class TestSerialEncoding(unittest.TestCase):
    b_msg_id = 20
    b_source_id = 5
    b_destination_id = 7
    b_order_code = 1001
    b_type = "COMMAND"
    b_attributes = 0x01
    b_format_string = ''
    b_incoming_data = []
    b_status = 0

    b_contents = {"COMMAND": 1001}
    s = PCOMMessage(msg_id=b_msg_id, from_=b_source_id, to=b_destination_id,
                    response_code=b_order_code, msg_type=b_type, attributes=b_attributes, msg_status=b_status,
                    data_fmt=b_format_string, data=b_incoming_data, contents=b_contents)

    command_msg = {
        'TOPICS': {
            'TX_TYPE': "DIRECT",
            'MSG_TYPE': "COMMAND",
            'RESPONSE_REQ': True,
            'MSG_ID': 100,
            'MSG_STATUS': "OK",
            'FROM': 0xfefe,
            'TO': 260
        },
        'CONTENTS': {
            'COMMAND': 2000
        }
    }

    def test_binary_unpacking(self):

        b_msg = serial_encoding.encode_pcom_message(self.s)
        msg = serial_encoding.decode_pcom_message(b_msg)

        self.assertEqual(msg.msg_id, self.b_msg_id)
        self.assertEqual(msg.from_, self.b_source_id)
        self.assertEqual(msg.to, self.b_destination_id)
        self.assertEqual(msg.response_code, self.b_order_code)
        # self.assertEqual(msg.msg_type, self.b_type)
        self.assertEqual(msg.attributes, self.b_attributes)
        self.assertEqual(msg.format_string, '')
        self.assertEqual(msg.data, [])

        self.s.format_string = "B"
        self.s.data = [0x10]

        b_msg = serial_encoding.encode_pcom_message(self.s)
        msg = serial_encoding.decode_pcom_message(b_msg)

        self.assertEqual(msg.msg_id, self.b_msg_id)
        self.assertEqual(msg.from_, self.b_source_id)
        self.assertEqual(msg.to, self.b_destination_id)
        self.assertEqual(msg.response_code, self.b_order_code)
        # self.assertEqual(msg.msg_type, self.b_type)
        self.assertEqual(msg.attributes, self.b_attributes)
        self.assertEqual(msg.format_string, "B")
        self.assertEqual(msg.data, [0x10])

        self.s.format_string = "fB"
        self.s.data = [0x10, 0x14]

        b_msg = serial_encoding.encode_pcom_message(self.s)
        msg = serial_encoding.decode_pcom_message(b_msg)

        self.assertEqual(msg.msg_id, self.b_msg_id)
        self.assertEqual(msg.from_, self.b_source_id)
        self.assertEqual(msg.to, self.b_destination_id)
        self.assertEqual(msg.response_code, self.b_order_code)
        # self.assertEqual(msg.msg_type, self.b_type)
        self.assertEqual(msg.attributes, self.b_attributes)
        self.assertEqual(msg.format_string, "fB")
        self.assertEqual(msg.data, [0x10, 0x14])

        self.s.format_string = "ff?BH"
        self.s.data = [0x01, 0x01, 0x01, 0x01, 0x01]

        b_msg = serial_encoding.encode_pcom_message(self.s)
        msg = serial_encoding.decode_pcom_message(b_msg)

        self.assertEqual(msg.msg_id, self.b_msg_id)
        self.assertEqual(msg.from_, self.b_source_id)
        self.assertEqual(msg.to, self.b_destination_id)
        self.assertEqual(msg.response_code, self.b_order_code)
        # self.assertEqual(msg.msg_type, self.b_type)
        self.assertEqual(msg.attributes, self.b_attributes)
        self.assertEqual(msg.format_string, "ff?BH")
        self.assertEqual(msg.data, [1.0, 1.0, 1, 1, 1])

    def test_translate_format_string(self):
        self.assertEqual('B4s', serial_encoding.translate_fmt_str('Bs', '\x12\x65\x65\x65\x00'))
        self.assertEqual('H4s', serial_encoding.translate_fmt_str('Hs', [12, "car"]))
        self.assertEqual('2H4s', serial_encoding.translate_fmt_str('2Hs', [12, 14, "car"]))
        self.assertEqual('2B4s', serial_encoding.translate_fmt_str('2Bs', '\x12\x14\x65\x65\x65\x00'))
        self.assertEqual('2b2H4s', serial_encoding.translate_fmt_str('2b2Hs', [12, 13, 14, 15, "car"]))
        self.assertEqual('2H4s', serial_encoding.translate_fmt_str('2Hs', '\x12\x13\x14\x15\x65\x65\x65\x00'))
        self.assertEqual('5s', serial_encoding.translate_fmt_str('s', ["help"]))
        self.assertEqual('5s', serial_encoding.translate_fmt_str('s', '\x65\x65\x65\x65\x00'))
        self.assertEqual('2s', serial_encoding.translate_fmt_str('s', ["c"]))
        self.assertEqual('2s', serial_encoding.translate_fmt_str('s', '\x23\x00'))
        self.assertEqual('5s2B', serial_encoding.translate_fmt_str('s2B', '\x65\x65\x65\x65\x00\x12\x12'))
        self.assertEqual('6s2H', serial_encoding.translate_fmt_str('s2H', ["hello", 12, 2]))
        self.assertEqual('2b', serial_encoding.translate_fmt_str('*b', [[12, 2]]))
        self.assertEqual('4I', serial_encoding.translate_fmt_str('*I', [[12, 2, 4, 5]]))
        self.assertEqual('2I', serial_encoding.translate_fmt_str('*I', '\x12\x12\x12\x12\x33\x33\x33\x33'))
        self.assertEqual('6B', serial_encoding.translate_fmt_str('*B', '\x65\x65\x65\x65\x65\x65'))
        self.assertEqual('H2b', serial_encoding.translate_fmt_str('H*b', '\x11\x11\x22\x22'))
        self.assertEqual('10H', serial_encoding.translate_fmt_str('*H', [[10, 0, 1, 2, 4, 1, 10, 6, 1, 6]]))
        self.assertEqual('b2H', serial_encoding.translate_fmt_str('b*H', [100, [100, 200]]))
        self.assertEqual('?', serial_encoding.translate_fmt_str('?', [1]))

    def test_cast_data(self):
        self.assertEqual([12, 13, 14], serial_encoding.cast_data("3H", ["12", "13", "14"]))
        self.assertEqual([1, 0, 1, 2, 2, 2, 2], serial_encoding.cast_data("3H4b", ["1", "0", "1", "2", "2", "2", "2"]))
        self.assertEqual(["hello"], serial_encoding.cast_data("s", ["hello"]))
        self.assertEqual([0, 0, 0, "hello"], serial_encoding.cast_data("3Hs", ["0", "0", "0", "hello"]))
        self.assertEqual([0, 0, "hello", 0, 0], serial_encoding.cast_data("2Hs2H", ["0", "0", "hello", "0", "0"]))
        self.assertEqual([12, "t", "s", 12], serial_encoding.cast_data("HssH", ["12", "t", "s", "12"]))
        self.assertEqual([12, 13, 14, 11, 0, 1, 2, 3],
                         serial_encoding.cast_data("2b2B2h2H", ["12", "13", "14", "11", "0", "1", "2", "3"]))
        self.assertEqual(['c', 32], serial_encoding.cast_data("cI", ["c", "32"]))
        self.assertEqual([[32, 45, 55]], serial_encoding.cast_data("*b", ["32, 45, 55"]))
        self.assertEqual([[11]], serial_encoding.cast_data('*B', ["11"]))
        self.assertEqual([['a']], serial_encoding.cast_data('*c', ["a"]))
        self.assertEqual([['a', 'b']], serial_encoding.cast_data('*c', ["a, b"]))
        self.assertEqual([['a', 'b']], serial_encoding.cast_data('*c', ["a,b"]))
        self.assertEqual([[0x12, 0x13]], serial_encoding.cast_data('*b', ["0x12, 0x13"]))
        self.assertEqual([[120, 10000, 30000]], serial_encoding.cast_data('*I', ["120, 10000, 30000"]))
        self.assertEqual([[0x45, 0x78, 0x10]], serial_encoding.cast_data('*B', ["0x45,0x78,0x10"]))
        self.assertEqual([[0xff, 0xfe, 0x10]], serial_encoding.cast_data('*B', ["0xff, 0xfe, 0x10"]))
        self.assertEqual([[10, 0xfe, 15]], serial_encoding.cast_data('*B', ["10, 0xfe, 15"]))
        self.assertEqual([[5.5, 7.8, 8.8, 9.9]], serial_encoding.cast_data('*f', ["5.5, 7.8, 8.8, 9.9"]))
        self.assertEqual([["hello", "goodbye"]], serial_encoding.cast_data('*s', ["hello,goodbye"]))
        self.assertEqual([[9.8888, 1.2222]], serial_encoding.cast_data('*d', ["9.8888, 1.2222"]))
        self.assertEqual([[True, False, True]], serial_encoding.cast_data('*?', ["True, False, True"]))
        self.assertEqual([[True, True, True]], serial_encoding.cast_data('*?', ["1, true, True"]))
        self.assertEqual([[False, False, False]], serial_encoding.cast_data('*?', ["0, False, false"]))
        self.assertEqual([[False, False, False]], serial_encoding.cast_data('*?', ["no, False, 0"]))
        self.assertEqual([[True]], serial_encoding.cast_data('*?', ["True"]))
        self.assertEqual([True], serial_encoding.cast_data('?', ["1"]))
        self.assertEqual([[False]], serial_encoding.cast_data('*?', ["false"]))
        self.assertEqual([[0x1111, 0x2222]], serial_encoding.cast_data('*H', ["0x1111, 0x2222"]))
        self.assertEqual([[0x1919, 0x2020, 0x3030]], serial_encoding.cast_data('*h', ["0x1919, 0x2020, 0x3030"]))
        self.assertEqual([[200, 100, 300, 400]], serial_encoding.cast_data('*i', ["200, 100, 300, 400"]))
        self.assertEqual([[1000, 2000, 3000, 0000, 4000]], serial_encoding.cast_data('*I', ["1000, 2000, 3000, 0000, 4000"]))
        self.assertEqual([[2000200, 3003000, 40004000]], serial_encoding.cast_data('*q', ["2000200, 3003000, 40004000"]))
        self.assertEqual([[10000000, 20000000, 300000000]], serial_encoding.cast_data('*Q', ["10000000, 20000000, 300000000"]))
        self.assertEqual([1000], serial_encoding.cast_data('H', [1000]))
        self.assertEqual([[10000000, 20000000, 300000000]],
                         serial_encoding.cast_data('*Q', [[10000000, 20000000, 300000000]]))

    def test_p_wrap(self):
        self.assertEqual(enums.START_BYTE_STR+'\x00'+enums.END_BYTE_STR, serial_encoding.p_wrap('\x00'))
        self.assertEqual(enums.START_BYTE_STR + '\x00\x01\x04\x05' + enums.END_BYTE_STR, serial_encoding.p_wrap('\x00\x01\x04\x05'))
        self.assertEqual('\x02\x10\x12\x03', serial_encoding.p_wrap(bytearray(enums.START_BYTE_STR)))
        self.assertEqual('\x02\x10\x13\x03', serial_encoding.p_wrap(bytearray(enums.END_BYTE_STR)))
        self.assertEqual('\x02\x10\x20\x03', serial_encoding.p_wrap(bytearray(enums.ESCAPE_BYTE_STR)))

    def test_expand_fmt_string(self):
        self.assertEqual("HHH", serial_encoding.expand_fmt_string("3H"))
        self.assertEqual("BB", serial_encoding.expand_fmt_string("2B"))
        self.assertEqual("HHHHHHHB", serial_encoding.expand_fmt_string("3H4H1B"))
        self.assertEqual("s", serial_encoding.expand_fmt_string("s"))
        self.assertEqual("QQQHH", serial_encoding.expand_fmt_string("3Q2H"))
        self.assertEqual("IIHHcc", serial_encoding.expand_fmt_string("2I2H2c"))
        self.assertEqual("iiiiII", serial_encoding.expand_fmt_string("4i2I"))
        self.assertEqual("ffHHf", serial_encoding.expand_fmt_string("2f2Hf"))
        self.assertEqual("?????", serial_encoding.expand_fmt_string("5?"))
        self.assertEqual("ssssc", serial_encoding.expand_fmt_string("4s1c"))
        self.assertEqual("ddffII", serial_encoding.expand_fmt_string("2d2f2I"))
        self.assertEqual("HIHf", serial_encoding.expand_fmt_string("HIHf"))

    def test_convert_to_bool(self):
        self.assertEqual(True, serial_encoding.convert_to_bool("True"))
        self.assertEqual(True, serial_encoding.convert_to_bool("true"))
        self.assertEqual(True, serial_encoding.convert_to_bool("1"))
        self.assertEqual(False, serial_encoding.convert_to_bool("False"))
        self.assertEqual(False, serial_encoding.convert_to_bool("false"))
        self.assertEqual(False, serial_encoding.convert_to_bool("0"))

    def test_serialize_response_code(self):
        test_pcom_msg = PCOMMessage(msg_type="COMMAND", contents={"COMMAND": 2000})
        self.assertEqual(2000, serial_encoding.serialize_response_code(test_pcom_msg))

        test_pcom_msg.msg_type = "RESPONSE"
        test_pcom_msg.contents = {"STATUS": 0}
        self.assertEqual(0, serial_encoding.serialize_response_code(test_pcom_msg))

        test_pcom_msg.msg_type = "STREAM"
        test_pcom_msg.contents = {"STREAM": 100}
        self.assertEqual(100, serial_encoding.serialize_response_code(test_pcom_msg))

        test_pcom_msg.msg_type = "INVALID"
        self.assertRaises(Exception, lambda: serial_encoding.serialize_response_code(test_pcom_msg))

        test_pcom_msg.msg_type = "PROPERTY"
        test_pcom_msg.contents = {"PROPERTY": 1000}
        self.assertEqual(1000, serial_encoding.serialize_response_code(test_pcom_msg))
        test_pcom_msg.contents = {"COMMAND" : 1000}
        self.assertEqual(None, serial_encoding.serialize_response_code(test_pcom_msg))

    def test_serialize_msg_type(self):
        PROPERTY_GET = enums.MessageCategory.Order << enums.CATEGORY_SHIFT | enums.OrderSubType.Property << enums.SUB_TYPE_SHIFT \
                       | enums.OrderPropertyOption.Get_Property << enums.OPTION_SHIFT
        PROPERTY_SET = enums.MessageCategory.Order << enums.CATEGORY_SHIFT | enums.OrderSubType.Property << enums.SUB_TYPE_SHIFT \
                       | enums.OrderPropertyOption.Set_Property << enums.OPTION_SHIFT
        STREAM_ON = enums.MessageCategory.Order << enums.CATEGORY_SHIFT | enums.OrderSubType.Property << enums.SUB_TYPE_SHIFT \
                       | enums.OrderPropertyOption.Stream_On << enums.OPTION_SHIFT
        STREAM_OFF = enums.MessageCategory.Order << enums.CATEGORY_SHIFT | enums.OrderSubType.Property << enums.SUB_TYPE_SHIFT \
                    | enums.OrderPropertyOption.Stream_Off << enums.OPTION_SHIFT

        test_pcom_msg = PCOMMessage(msg_type="COMMAND", contents={"COMMAND": 2000})
        self.assertEqual(0x00, serial_encoding.serialize_msg_type(test_pcom_msg))

        test_pcom_msg.msg_type = "PROPERTY"
        test_pcom_msg.contents = {"ACTION": "GET", "PROPERTY": 1000}
        self.assertEqual(PROPERTY_GET, serial_encoding.serialize_msg_type(test_pcom_msg))

        test_pcom_msg.contents = {"ACTION": "SET", "PROPERTY": 2000, "VALUE": 10}
        self.assertEqual(PROPERTY_SET, serial_encoding.serialize_msg_type(test_pcom_msg))

        test_pcom_msg.msg_type = "STREAM"
        test_pcom_msg.contents = {"RATE": 1000, "STOP": False}
        self.assertEqual(STREAM_ON, serial_encoding.serialize_msg_type(test_pcom_msg))

        test_pcom_msg.contents = {"RATE": 1000, "STOP": True}
        self.assertEqual(STREAM_OFF, serial_encoding.serialize_msg_type(test_pcom_msg))

    def test_ack_nak_message(self):
        seq_num = 0
        self.assertEqual('\x20\xe0\x00\x00', serial_encoding.ack_nak_message(seq_num, True))
        seq_num += 12
        self.assertEqual('\x2c\xd4\x00\x00', serial_encoding.ack_nak_message(seq_num, True))
        seq_num = 15
        self.assertEqual('\x3f\xc1\x00\x00', serial_encoding.ack_nak_message(seq_num, False))

    def test_get_str_len(self):
        """
        NOTE: string must be NULL terminated
        :return:
        """
        self.assertEqual(4, serial_encoding.get_str_len('\x60\x61\x62\x00'))
        self.assertEqual(1, serial_encoding.get_str_len('\x00'))
        self.assertEqual(2, serial_encoding.get_str_len('\x60\x00'))
        self.assertEqual(3, serial_encoding.get_str_len('\x60\x61\x00'))

    def test_escape_packet(self):
        return

    def test_wrap_packet(self):
        self.assertEqual('\x02\x80\x80\x00\x00\x03', serial_encoding.wrap_packet('', 0, True))
        self.assertEqual('\x02\x81\x4b\x01\x00\x33\x03', serial_encoding.wrap_packet('\x33', 1, True))

    def test_checksum(self):
        sum_p = serial_encoding.sum_packet(range(0, 100)*100)
        check_s = serial_encoding.get_checksum(sum_p)
        self.assertEqual(0, sum_p+check_s & 0xff)



class TestPCOMMessage(unittest.TestCase):
    command_msg = {
        'TOPICS': {
            'TX_TYPE': "DIRECT",
            'MSG_TYPE': "COMMAND",
            'RESPONSE_REQ': True,
            'MSG_ID': 100,
            'MSG_STATUS': "OK",
            'FROM': 0xfefe,
            'TO': 260
        },
        'CONTENTS': {
            'COMMAND': 2000
        }
    }

    PROPERTY_MSG = {
        'TOPICS': {
            'TX_TYPE': None,
            'MSG_TYPE': None,
            'RESPONSE_REQ': None,
            'MSG_ID': None,
            'MSG_STATUS': None,
            'FROM': None,
            'TO': None
        },
        'CONTENTS': {
            'PROPERTY': None,
            'VALUE': None,
            'ACTION': None
        }
    }

    RESPONSE_MSG = {
        'TOPICS': {
            'TX_TYPE': None,
            'MSG_TYPE': None,
            'RESPONSE_REQ': None,
            'MSG_ID': None,
            'MSG_STATUS': None,
            'FROM': None,
            'TO': None
        },
        'CONTENTS': {
            'STATUS': None,
            'STATUS_NAME': None
        }
    }

    PROPERTY_NAME_MSG = {u'TOPICS': {u'TO': 343, u'MSG_ID': 7, u'FROM': u'qt.SelfTest', u'MSG_TYPE': u'PROPERTY'},
                         u'CONTENTS': {u'PROPERTY': 'test_property', u'ACTION': 'GET'}}

    PROPERTY_ID_MSG = {u'TOPICS': {u'TO': 343, u'MSG_ID': 7, u'FROM': u'qt.SelfTest', u'MSG_TYPE': u'PROPERTY'},
                       u'CONTENTS': {u'PROPERTY': 1100, u'ACTION': 'GET'}}

    COMMAND_NAME_MSG = {u'TOPICS': {u'TO': 343, u'MSG_ID': 7, u'FROM': u'qt.SelfTest', u'MSG_TYPE': u'COMMAND'},
                       u'CONTENTS': {u'COMMAND': 'test_command'}}

    def test_property_and_command_names(self):
         TEST_ITEM_ID = 343
         TEST_PROPERTY_MAP = {TEST_ITEM_ID: {'test_property': 1100}}
         TEST_COMMAND_MAP = {TEST_ITEM_ID: {'test_command': 100}}

         self.assertEqual(PCOMMessage._look_up_id(TEST_PROPERTY_MAP, TEST_ITEM_ID, 'test_property'), 1100)
         self.assertEqual(PCOMMessage._look_up_id(TEST_PROPERTY_MAP, TEST_ITEM_ID, 1100), 1100)
         self.assertEqual(PCOMMessage._look_up_id(TEST_COMMAND_MAP, TEST_ITEM_ID, 100), 100)
         self.assertEqual(PCOMMessage._look_up_id(TEST_COMMAND_MAP, TEST_ITEM_ID, 'test_command'), 100)

         pcom_serial.property_name_map = {TEST_ITEM_ID: {'test_property': 1100}}
         pcom_serial.command_name_map = {TEST_ITEM_ID: {'test_command': 100}}

         EXPECTED_PROPERTY_OUTPUT_BUFFER = '\x07\x00\x00\xfc\x57\x01\x4c\x04\x00\x00\x10\x00\x00'
         EXPECTED_COMMAND_OUTPUT_BUFFER = '\x07\x00\x00\xfc\x57\x01\x64\x00\x00\x00\x00\x00\x00'

         msg = PCOMMessage.from_json_msg(self.PROPERTY_NAME_MSG)
         self.assertEqual(EXPECTED_PROPERTY_OUTPUT_BUFFER, serial_encoding.encode_pcom_message(msg))

         msg = PCOMMessage.from_json_msg(self.PROPERTY_ID_MSG)
         self.assertEqual(EXPECTED_PROPERTY_OUTPUT_BUFFER, serial_encoding.encode_pcom_message(msg))

         msg = PCOMMessage.from_json_msg(self.COMMAND_NAME_MSG)
         self.assertEqual(EXPECTED_COMMAND_OUTPUT_BUFFER, serial_encoding.encode_pcom_message(msg))



