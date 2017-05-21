from parlay.server.reactor import reactor, run_in_reactor
from .parlay_script import ParlayScript, DEFAULT_ENGINE_WEBSOCKET_PORT, start_script
from threading import Thread
import inspect
import time
import datetime
global script, THREADED_REACTOR

# get the script name importing me so it can have an ID
script_name = inspect.stack()[0][1]


THREADED_REACTOR = reactor
THREADED_REACTOR.getThreadPool()

script = None

class ThreadedParlayScript(ParlayScript):

    ready = False

    def _start_script(self):
        global script
        ThreadedParlayScript.ready = True
        script = self  # so everyone knows we're THE script
        # do nothing. This is just an appliance class that doesn't run anything
        pass

def start_reactor(ip, port):
    try:
        global THREADED_REACTOR
        # This is the reactor we will be using in a separate thread
        THREADED_REACTOR.callWhenRunning(lambda: start_script(ThreadedParlayScript, ip, port,
                                                              stop_reactor_on_close=True, reactor=THREADED_REACTOR))
        THREADED_REACTOR._registerAsIOThread = False
        THREADED_REACTOR.run(installSignalHandlers=False)
        print("DONE REACTING")
    except Exception as e:
        print(e)


def setup(ip='localhost', port=DEFAULT_ENGINE_WEBSOCKET_PORT, timeout=3):
    """
    Connect this script to the broker's websocket server.

    :param ip: ip address of the broker websocket server
    :param port: port of the broker websocket server
    :param timeout: try for this long to connect to broker before giving up
    :return: none
    """
    global script, THREADED_REACTOR
    # **ON IMPORT** start the reactor in a separate thread
    if not THREADED_REACTOR.running:
        r = Thread(target=start_reactor, args=(ip, port))
        r.daemon = True
        r.start()
        # wait till we're ready
        start = datetime.datetime.now()
        print("Connecting to", ip, ":", port)
        while THREADED_REACTOR is None or (not THREADED_REACTOR.running) or not ThreadedParlayScript.ready:
            time.sleep(0.001)
            if (datetime.datetime.now() - start).total_seconds() > timeout:
                raise RuntimeError("Could not connect to parlay. Is the parlay system running?")

def run_in_threaded_reactor(fn):
    """
    Decorator to run the decorated function in the threaded reactor.
    """
    return run_in_reactor(THREADED_REACTOR)(fn)