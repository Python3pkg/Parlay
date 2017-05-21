from .meta_protocol import ProtocolMeta
import inspect
from twisted.internet import defer
from parlay.server.broker import run_in_broker, run_in_thread
from parlay.protocols.utils import timeout,PrivateDeferred
from collections import deque



class BaseProtocol(object, metaclass=ProtocolMeta):
    """
    This the base protocol that *all* parlay protocols must inherit from. Subclass this to make custom protocols to
    talk to 3rd party equipment.
    """

    def __init__(self):
        self._new_data = PrivateDeferred()
        self.items = getattr(self, "items", [])

    @classmethod
    def open(cls, adapter):
        """
        Override this with a generic method that will open the protocol.
        The first argument must be the broker, the rest will be parameters that the user can set. Default arguments will
        be sent to the UI and can be overwritten bu the user
        It must return the built protocol (subclass of Protocol)  that will be registered with the Broker
        Be sure to decorate it with @classmethod

        e.g.
        @classmethod
        def open(cls, adapter, ip, port=8080):
            return protocol(ip,port)
        """
        raise NotImplementedError()

    def close(self):
        """
        Override this with a generic method that will close the protocol.

        e.g.
        @classmethod
        def close():
        """
        raise NotImplementedError()

    @classmethod
    def get_open_params(cls):
        """
        Returns the params for the cls.open() class method. Feel free to overwrite this in a sub-class if this default
        implementation doesn't fit your protocol's needs.
        :return: A list of parameter names
        :rtype: list
        """
        # get the arguments
        # (don't use argspec because it is needlesly strict and fails on perfectly valid Cython functions)
        args, varargs, varkw = inspect.getargs(cls.open.__code__)
        return args[2:]  # remove 'cls' and 'broker'

    @classmethod
    def get_open_params_defaults(cls):
        """
        return the defaults for parameters to the cls.open() using inspect. Feel free to overwrite this in a sub-class
        if this default implementation doesn't fit your protocol's needs.
        :return: A dictionary of parameter names -> default values.
        :rtype: dict
        """
        # (don't use argspec because it is needlesly strict and fails on perfectly valid Cython functions)
        defaults = cls.open.__defaults__ if cls.open.__defaults__ is not None else []
        params = cls.get_open_params()
        # cut params to only the last x (defaults are always at the end of the signature)
        params = params[len(params) - len(defaults):]
        return dict(list(zip(params, defaults)))

    def get_protocol_discovery_meta_info(self):
        """
        This will return protocol meta-info that will be returned with every discovery message.
        This is a good place to store things like enuemrations or protocol status to pass to the UI
        """
        return {}

    def get_discovery(self):
        """
        This will get called when a discovery message is sent out. Return a deferred that will be called back with
        all attached:
        item types, message types, and connected item instances
        """
        return {'TEMPLATE': 'Protocol',
                'NAME': str(self),
                'protocol_type': getattr(self, "_protocol_type_name", "UNKNOWN"),
                'CHILDREN': [x.get_discovery() for x in self.items]}

    def get_new_data_wait_handler(self):
        return WaitHandler(self._new_data)

    @run_in_broker
    def got_new_data(self, data):
        """
        Call this when you have new data and want to pass it to any waiting Items
        """
        print(data)
        old_new_data = self._new_data

        # setup the new data in case it causes a callback to fire
        self._new_data = PrivateDeferred()
        old_new_data._callback(data)


class WaitHandler(object):
    """
    An Object used to do safe cross thread waits on deferreds
    """
    def __init__(self, deferred):
        self._deferred = deferred

    @run_in_broker
    def addCallback(self, fn, async=True):
        """
        Add a callback when you get new data
        """
        if not async:
            fn = run_in_thread(fn)
        self._deferred.addCallback(fn)

    @run_in_broker
    def wait(self, timeout_secs=None):
        """
        Call this to wait until there is data from the protocol.
        If threaded: Will block. Return value is serial line data
        If Async   : Will not block. Return value is Deferred that will be called back with  data
        :param timeout_secs : Timeout if you don't get data in time. None if no timeout
        :type timeout_secs : int|None
        """
        return timeout(self._deferred, timeout_secs)
