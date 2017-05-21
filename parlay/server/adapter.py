from twisted.internet import reactor, defer
from parlay.protocols.meta_protocol import ProtocolMeta
import sys


class Adapter(object):
    """
    Adapters connect outside systems to the Broker.  The Broker *only* connects with Adapters.
    Adapters handle opening protocols in their system, discovery of their system and
    relaying pub/sub messages to the broker.

    Examples of supported adapters are: WebsocketServerAdapter, Pyadapter, FileTransportServerAdapter
    """

    def __init__(self):
        self._items = getattr(self, '_items', [])  # default to [] if a subclass hasn't set it
        self.reactor = getattr(self, 'reactor', reactor)
        self._connected = defer.Deferred()

        self.open_protocols = []  # list of protocols that *ARE* open

    def publish(self, msg, callback=None):
        """
        :param msg: Parlay message to publish
        :type msg: dict
        :param callback: Optional the callback function to call if the broker responds directly
        :type callback: function
        :return: None
        """
        raise NotImplementedError()

    def subscribe(self, fn, **kwargs):
        """
        Subscribe to messages matching the provided topic keyword/value pairs
        :param fn: the listener function to call with messages meeting the criteria
        :type fn: function
        :param kwargs: The topics and their values to subscribe to
        :type kwargs: dict
        :return: None
        """
        raise NotImplementedError()

    def register_item(self, item):
        """
        Register an item with the adapter
        :param item: item object to register
        :return: None
        """
        self._items.append(item)
        # let the item know we're their adapter
        item._adapter = self

    def get_protocols(self):
        """
        Return a list of protocols that could potentially be opened.
        Return a deferred if this is not ready yet
        :rtype defer.Deferred
        """
        raise NotImplementedError()

    def get_open_protocols(self):
        raise NotImplementedError()

    def open_protocol(self, protocol_name, protocol_args):
        """
        open the protocol with the name protocol_name or raise a LookupError if
        there is no such protocol by that name
        :raise LookupError
        :arg protocol_name : The name of the protocol to open
        :arg protocol_args : A dict of key value pairs for the opening arguments
        :type protocol_name : str
        :type protocol_args : dict
        :rtype defer.Deferred
        """
        # NOTE: Closing a protocol is done from the Protocol.close() function
        raise LookupError()

    def discover(self, force):
        """
        Return the discovery list (or a deferred) for all protocols and items attached to this adapter.

        The final return value from this function (or its deferred) is a list of discovery dictionaries,
        as specified under the "Item Discovery" section of the Parlay Standard Item Communications
        Specification.

        :type force bool
        :param force True if this requested discover action wants to clear any caches and do a fresh discover.
        :rtype defer.Deferred
        """
        raise NotImplementedError()


class PyAdapter(Adapter):
    """
    Adapter for the Python Broker and Python environment
    """

    def __init__(self, broker):
        self._broker = broker
        self.reactor = broker.reactor
        self.open_protocols = []  # list of protocols that are currently open
        self._discovery_cache = {}  # dict: K->V = Protocol -> discovery

        super(PyAdapter, self).__init__()

    def publish(self, msg, callback=None):
        # publish the message, and if the broker needs to respond he can publish it himself
        self._broker.publish(msg, callback)

    def subscribe(self, fn, **kwargs):
        self._broker.subscribe(fn, **kwargs)

    def track_open_protocol(self, protocol):
        """
        track the given protocol for discovery
        """
        if protocol not in self.open_protocols:
            self.open_protocols.append(protocol)

    def untrack_open_protocol(self, protocol):
        """
        Untracks the given protocol. You must call this when a protocol has closed to clean up after it.
        """
        self._broker.unsubscribe_all(protocol)
        try:
            self.open_protocols.remove(protocol)
        except ValueError:
            pass

    def get_protocols(self):
        """
        Return a list of protocols that could potentially be opened
        """
        reg = ProtocolMeta.protocol_registry
        # make a dictionary of protocol names (keys) to (values) a dictionary of open params and defaults
        protocols = {k: {} for k in list(reg.keys())}
        for name in list(protocols.keys()):
            protocols[name]["params"] = reg[name].get_open_params()
            protocols[name]["defaults"] = reg[name].get_open_params_defaults()

        return protocols

    def get_open_protocols(self):
        """
        Returns a list of protocol object that are currently open
        :return:
        """
        return self.open_protocols

    def open_protocol(self, protocol_name, open_params):
        """
        open the protocol with the name protocol_name or raise a LookupError if
        there is no such protocol by that name
        :raise LookupError
        :arg protocol_name : The name of the protocol to open
        :arg open_params : A dict of key value pairs for the opening arguments
        :type protocol_name : str
        :type open_params : dict
        """
        # make sure we know about the protocol
        if protocol_name not in ProtocolMeta.protocol_registry:
            raise LookupError(str(protocol_name)+" not in protocol registry. Is there a typo?")

        else:
            # we have the protocol! open it
            protocol_class = ProtocolMeta.protocol_registry[protocol_name]
            d = defer.maybeDeferred(protocol_class.open, self, **open_params)

            # append to list on success
            def ok(p):
                if p is not None:
                    self.track_open_protocol(p)
                return p

            d.addCallback(ok)
            return d

    def discover(self, force):
        """
        Return the discovery (or a deferred) for all protocols and items attached to this adapter
        :type force bool
        :param force True if this requested discover action wants to clear any caches and do a fresh discover.
        """
        # clear the cache if we're told to force
        if force:
            self._discovery_cache = {}

        d_list = []
        for p in self.open_protocols:
            # if it's already in the cache, then just return it, otherwise, get it from the protocol
            if p in self._discovery_cache:
                d = defer.Deferred()
                d.callback(self._discovery_cache[p])
            else:
                d = defer.maybeDeferred(p.get_discovery)

            # add this protocols discovery
            def callback(disc, error=None, protocol=p):
                protocol_discovery = disc

                if error is not None:
                    protocol_discovery = {'error': str(error)}

                if type(disc) is dict:
                    # add it to the cache
                    self._discovery_cache[protocol] = protocol_discovery
                    return protocol_discovery
                else:
                    sys.stderr.write("ERROR: Discovery must return a dict, instead got: " + str(disc) +
                                     " from " + str(protocol))

            d.addCallback(callback)
            d.addErrback(lambda err: callback({}, error=err))

            d_list.append(d)

        # wait for all to be finished
        all_d = defer.gatherResults(d_list, consumeErrors=False)
        return all_d
