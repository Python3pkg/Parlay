from parlay.testing.unittest_mixins.adapter import AdapterMixin
from parlay.testing.unittest_mixins.reactor import ReactorMixin

from twisted.trial import unittest
from twisted.internet import defer
from twisted.python import failure
from twisted.internet.task import Clock
from parlay.server.broker import Broker, run_in_thread

from parlay.protocols import base_protocol
import time

class BaseProtocolTest(unittest.TestCase, ReactorMixin):

    def setUp(self):
        self.protocol = base_protocol.BaseProtocol()
        self.clock = Clock()

    def testSimpleWaitAsync(self):
        data = "This is Test DATA!"  # the input
        data_out = []  # the output
        self.protocol.get_new_data_wait_handler().addCallback(lambda m: data_out.append(m), async=True)
        self.protocol.got_new_data(data)
        self.assertEqual(data, data_out[0])

    def testSimpleWaitSync(self):
        data = "This is Test DATA!"  # the input
        data_out = []  # the output

        @run_in_thread
        def run_test():
            handle = self.protocol.get_new_data_wait_handler()
            self.protocol.got_new_data(data)
            data_out.append(handle.wait())

        # should be a deferred since we're in the broker thread
        d = run_test()
        d.addCallback(lambda _: self.assertEqual(data, data_out[0]))

        #return d so it will wait until we're done
        return d

    def testTimeoutSync(self):
        from parlay.protocols.utils import TimeoutError
        @run_in_thread
        def run_test():
            handle = self.protocol.get_new_data_wait_handler()
            handle.wait(.001)

        d = run_test()
        self.assertFailure(d, TimeoutError)
        return d

    def testCrossThreadSuccess(self):
        data = "This is Test DATA!"  # the input
        data_out = []  # the output

        @run_in_thread
        def run_test():
            handle = self.protocol.get_new_data_wait_handler()
            data_out.append(handle.wait(0.5))
            print("done waiting")

        @run_in_thread
        def push_data():
            time.sleep(0.1)
            self.protocol.got_new_data(data)

        # should be a deferred since we're in the broker thread
        d = run_test()
        d.addCallback(lambda _: self.assertEqual(data, data_out[0]))

        push_data()
        return d

    def tearDown(self):
        pass
