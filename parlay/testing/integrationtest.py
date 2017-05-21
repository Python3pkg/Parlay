"""
Integration tests are when you want to use Parlay to test *something external*. It could be a simulator, or a real
device hooked up over a protocol. Either way, the broker must already be running, and the connections established before
running the Integration test
"""

import sys
import time
import logging
import unittest
import multiprocessing

from twisted.internet import defer, error

import parlay
from parlay.items.threaded_item import ListenerStatus
from parlay.utils import setup, scripting_setup, shutdown_broker


_BROKER_SETUP_WAIT_TIME = 0.2  # seconds
_BROKER_SETUP_TIMEOUT_TIME = 0.2  # seconds
_BROKER_SETUP_NUM_RETRIES = 10
_BROKER_SHUTDOWN_TIMEOUT_TIME = 5  # seconds


def start_broker():
    p = multiprocessing.Process(target=parlay.start, kwargs={"open_browser": False})
    p.start()
    return p


def stop_broker():
    shutdown_broker()


class TestCase(unittest.TestCase):
    """
    An integration test case is like a unittest test case, and uses the same reporting mechanisms.
    """

    def setUp(self):
        """
        If you override setUp, the function setup_integration_test MUST be called.
        :return: None
        """
        self.setup_integration_test()

    def tearDown(self):
        """
        If you override tearDown, the function teardown_integration_test MUST be called.
        :return: None
        """
        self.teardown_integration_test()

    def setup_integration_test(self):
        """
        Starts a broker in a different process, and connects this process to it via
        parlay's "setup" method.
        :return:
        """
        try:
            self.broker_process = start_broker()
            for _ in range(_BROKER_SETUP_NUM_RETRIES - 1):
                time.sleep(_BROKER_SETUP_WAIT_TIME)
                try:
                    setup(timeout=_BROKER_SETUP_TIMEOUT_TIME)
                except RuntimeError as _:
                    pass
            time.sleep(_BROKER_SETUP_WAIT_TIME)
            setup(timeout=_BROKER_SETUP_TIMEOUT_TIME)
        except Exception as e:
            self.teardown_integration_test()
            raise e

    def teardown_integration_test(self):
        """
        Stops the broker running in the separate process.
        :return:
        """
        try:
            stop_broker()
            if self.broker_process is not None:
                self.broker_process.join(timeout=_BROKER_SHUTDOWN_TIMEOUT_TIME)
        except Exception as _:
            logging.log(msg="Broker didn't shut down.  Killing broker process.", level=logging.WARNING)
        self.broker_process.terminate()
        self.broker_process.join(timeout=_BROKER_SHUTDOWN_TIMEOUT_TIME)
        if self.broker_process.is_alive():
            logging.log(msg="Broker won't terminate.  Integration test exiting.", level=logging.ERROR)
            sys.exit(1)
        self.broker_process = None

    def simulate_id(self, id):
        """
        Simulate a particular device so we can wait on the messages
        """
        # subscribe to messages with this ID
        scripting_setup.script.subscribe_to(TO=id)

    @scripting_setup.run_in_threaded_reactor
    def wait_for_message(self, from_id, to_id=None, timeout=30):
        """
        Wait for a message from from_id, and to to_id (or anyone if to_id is None)
        """
        d = defer.Deferred()
        timer = scripting_setup.THREADED_REACTOR.callLater(timeout, lambda: d.cancel())

        def listener(msg):
            if msg["TOPICS"].get("FROM", None) == from_id and \
                    (to_id is None or msg["TOPICS"].get("TO", None) == to_id):
                timer.cancel()
                d.callback(msg)
                return ListenerStatus.REMOVE_LISTENER
            else:
                return ListenerStatus.KEEP_LISTENER

        scripting_setup.script.add_listener(listener)
        return d

    def send_response(self, msg, contents=None, extra_topics=None):
        contents = contents if contents is not None else {}
        extra_topics = extra_topics if extra_topics is not None else {}

        # scripting_setup.script.send


