"""
Define a base class for creating a client script
"""
from twisted.internet import reactor as default_reactor
from twisted.python.failure import Failure
from twisted.internet.protocol import Factory
import sys, os
import traceback
from parlay.items.threaded_item import ThreadedItem, ITEM_PROXIES, ListenerStatus
from autobahn.twisted.websocket import WebSocketClientFactory
from parlay.protocols.websocket import WebsocketClientAdapter, WebsocketClientAdapterFactory

DEFAULT_ENGINE_WEBSOCKET_PORT = 8085


class ParlayScript(ThreadedItem):

    def __init__(self, item_id=None, name=None, _reactor=None, adapter=None):
        if item_id is None:
            # use the full file path as the ID, default to class name if unknown
            try:
                item_id = "script." + os.path.abspath(sys.modules['__main__'].__file__)
            except:
                item_id = "script." + self.__class__.__name__ + " (Unknown File)"

        if name is None:
            name = self.__class__.__name__ + ".py"
        # default script name and id to the name of this class
        ThreadedItem.__init__(self, item_id, name, _reactor, adapter=adapter)
        if adapter._connected.called:
            self._reactor.callLater(0, self._start_script)
        else:
            adapter._connected.addCallback(lambda _: self._start_script())


    def on_message(self, msg):
        self._runListeners(msg)

    def kill(self):
        """ Kill the current script """
        self.cleanup()

    def cleanup(self, *args):
        """
        Cleanup after running the script
        :param args:
        :return:
        """

        def internal_cleanup():
            self._adapter.transport.loseConnection()
            # should we stop the reactor on close?
            if self.__class__.stop_reactor_on_close:
                self._reactor.stop()

        self._adapter.sendClose()
        self._reactor.callLater(1, internal_cleanup)

    def _start_script(self):
        """ Init and run the script """
        # run the script and run cleanup after.
        defer = self.reactor.maybeDeferToThread(self._in_thread_run_script)
        defer.addBoth(self.cleanup)

    def _in_thread_run_script(self):
        """ Run the script. """
        try:
            self.run_script()

        except Exception as e:
            # handle any exception thrown
            exc_type,exc_value,exc_traceback = sys.exc_info()
            print("Exception Error:  ",  exc_value)
            print(e)

            # print traceback, excluding this file
            traceback.print_tb(exc_traceback)
            # exc_strings = traceback.format_list(traceback.extract_tb(exc_traceback))
            # exc_strings = [s for s in exc_strings if s.find("parlay_script.py")< 0 ]
            # for s in exc_strings:
            #     print s

    def shutdown_broker(self):
        self.send_parlay_message({"TOPICS": {"type": "broker", 'request': 'shutdown'}, "CONTENTS": {}})

    def run_script(self):
        """
        This should be overridden by the script class
        """
        raise NotImplementedError()


def start_script(script_class, engine_ip='localhost', engine_port=DEFAULT_ENGINE_WEBSOCKET_PORT,
                 stop_reactor_on_close=None, skip_checks=False, reactor=None):
    """
    Construct a new script from the script class and start it

    :param script_class : The ParlayScript class to run (Must be subclass of ParlayScript)
    :param engine_ip : The ip of the broker that the script will be running on
    :param engine_port : the port of the broker that the script will be running on
    :param stop_reactor_on_close: Boolean regarding whether ot not to stop the reactor when the script closes
    (Defaults to False if the reactor is running, True if the reactor is not currently running)
    :param skip_checks : if True will not do sanity checks on script (CAREFUL: BETTER KNOW WHAT YOU ARE DOING!)
    """
    if not skip_checks:
        if not issubclass(script_class, ParlayScript):
            raise TypeError("start_script called with: "+str(script_class)+" \n" +
                            "Can only call start_script on an instance of a subclass of ParlayScript")

    if reactor is None:
        reactor = default_reactor
    # get whether to stop the reactor or not (default to the opposite of reactor running)
    script_class.stop_reactor_on_close = stop_reactor_on_close if stop_reactor_on_close is not None else not reactor.running

    # connect it up
    factory = WebsocketClientAdapterFactory("ws://" + engine_ip + ":" + str(engine_port), reactor=reactor)
    adapter = factory.adapter
    script_item = script_class(_reactor=reactor, adapter=adapter)
    reactor.connectTCP(engine_ip, engine_port, factory)

    if not reactor.running:
        reactor.run()
