from twisted.trial import unittest
from twisted.internet import defer
from twisted.internet.task import Clock
from parlay.server.broker import Broker
from parlay.testing.unittest_mixins.adapter import AdapterMixin
from parlay.testing.unittest_mixins.reactor import ReactorMixin

from parlay.items import parlay_standard
from parlay import parlay_command


class PropertyTest(unittest.TestCase, AdapterMixin, ReactorMixin):

    def setUp(self):
        self.item = parlay_standard.ParlayCommandItem("TEST_ITEM", "TEST_ITEM",
                                                      reactor=self.reactor, adapter=self.adapter)
        self.prop_item = PropertyTestItem("PROPERTY_TEST_ITEM", "PROPERTY_TEST_ITEM",
                                          reactor=self.reactor, adapter=self.adapter)

    def testDiscovery(self):
        self.item.discover(force=True)
        expected = {"TOPICS": {'type': 'broker', 'request': 'get_discovery'}, "CONTENTS": {'force': True}}
        self.assertEqual(self.adapter.last_published, expected)

    def testSimpleProperty(self):
        self.prop_item.simple_property = 5
        self.assertEqual(self.prop_item.simple_property, 5)

    def testPropertyTypeCoercion(self):
        self.prop_item.simple_property = "5"
        self.assertEqual(self.prop_item.simple_property, 5)

    def testCustomRWProp(self):
        # make sure we're clean
        self.assertEqual(PropertyTestItem.custom_list, [])
        self.assertEqual(self.prop_item.custom_rw_propery, "")
        # add some to the list
        self.prop_item.custom_rw_propery = 1
        self.prop_item.custom_rw_propery = 2
        self.prop_item.custom_rw_propery = 3

        self.assertEqual(self.prop_item.custom_rw_propery, "1.0,2.0,3.0")

    def testPropertySpec_Set(self):
        # set to 5
        self.prop_item.simple_property = 5
        self.assertEqual(self.prop_item.simple_property, 5)
        self.prop_item.get_discovery()
        self.prop_item.on_message({"TOPICS": {"TO": "PROPERTY_TEST_ITEM", "MSG_TYPE": "PROPERTY", "FROM": "TEST", "MSG_ID": 100},
                              "CONTENTS": {"ACTION": "SET", "PROPERTY": "simple_property", "VALUE": 10}})

        self.assertEqual(self.prop_item.simple_property, 10)


    def testPropertySpec_Get(self):
        # set to 5
        self.prop_item.simple_property = 5
        self.assertEqual(self.prop_item.simple_property, 5)
        self.prop_item.get_discovery()
        self.prop_item.on_message({"TOPICS": {"TO": "PROPERTY_TEST_ITEM", "MSG_TYPE": "PROPERTY",
                                              "FROM": "TEST", "MSG_ID": 100},
                                   "CONTENTS": {"ACTION": "GET", "PROPERTY": "simple_property"}})
        print(self.adapter.last_published)
        self.assertEqual(self.adapter.last_published,
                         {'TOPICS': {'FROM': 'PROPERTY_TEST_ITEM', 'MSG_TYPE': 'RESPONSE',
                                     'MSG_STATUS': 'OK', 'MSG_ID': 100, 'TO': 'TEST', 'RESPONSE_REQ': False,
                                     'TX_TYPE': 'DIRECT'},
                          'CONTENTS': {'ACTION': 'RESPONSE', 'PROPERTY': 'simple_property', 'VALUE': 5}})

        self.prop_item.simple_property = 10
        self.assertEqual(self.prop_item.simple_property, 10)
        self.prop_item.get_discovery()
        self.prop_item.on_message({"TOPICS": {"TO": "PROPERTY_TEST_ITEM", "MSG_TYPE": "PROPERTY",
                                              "FROM": "TEST", "MSG_ID": 900},
                                   "CONTENTS": {"ACTION": "GET", "PROPERTY": "simple_property"}})
        print(self.adapter.last_published)
        self.assertEqual(self.adapter.last_published,
                         {'TOPICS': {'FROM': 'PROPERTY_TEST_ITEM', 'MSG_TYPE': 'RESPONSE',
                                     'MSG_STATUS': 'OK', 'MSG_ID': 900, 'TO': 'TEST', 'RESPONSE_REQ': False,
                                     'TX_TYPE': 'DIRECT'},
                          'CONTENTS': {'ACTION': 'RESPONSE', 'PROPERTY': 'simple_property', 'VALUE': 10}})

    def tearDown(self):
        # reset custom property list
        PropertyTestItem.custom_list = []


class CommandTest(unittest.TestCase, AdapterMixin, ReactorMixin):

    def setUp(self):
        self.cmd_item_1 = CommandTestItem("ITEM_1", "ITEM_1", reactor=self.reactor, adapter=self.adapter)
        self.cmd_item_2 = CommandTestItem("ITEM_2", "ITEM_2", reactor=self.reactor, adapter=self.adapter)

    def testLocalSyncCommand(self):
        d = defer.maybeDeferred(self.cmd_item_1.add, 2, 3)
        d.addCallback(self.assertEqual, 5)
        return d

    def testLocalAsyncCommand(self):
        value = self.cmd_item_1.add_async(2, 3)
        self.assertEqual(value, 5)


class PropertyTestItem(parlay_standard.ParlayCommandItem):
    """
    Helper class to test custom properties
    """

    simple_property = parlay_standard.ParlayProperty(val_type=int)
    read_only_property = parlay_standard.ParlayProperty(val_type=int, read_only=True)
    write_only_property = parlay_standard.ParlayProperty(val_type=str, write_only=True)
    #custom reader writer. Writer pushes into list and reader gives you that list as a string
    custom_list = []
    custom_rw_propery = parlay_standard.ParlayProperty(val_type=float,
                                                       custom_read=lambda: ','.join(str(x) for x in PropertyTestItem.custom_list),
                                                       custom_write=lambda x: PropertyTestItem.custom_list.append(x))


class CommandTestItem(parlay_standard.ParlayCommandItem):
    """
    Helper class to test custom commands
    """

    @parlay_command()
    def add(self, x, y):
        return x + y

    @parlay_command(async=True)
    def add_async(self, x, y):
        return x + y
