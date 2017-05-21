from parlay.protocols.base_protocol import BaseProtocol
from parlay.server.adapter import Adapter
from autobahn.twisted.websocket import WebSocketClientFactory, WebSocketServerProtocol, WebSocketClientProtocol
from parlay.server.broker import Broker
import json
from twisted.internet import defer
from twisted.internet.protocol import Factory


class WebSocketServerAdapter(WebSocketServerProtocol, Adapter):
    """
    When a client connects over a websocket, this is the protocol that will handle the communication.
    The messages are encoded as a JSON string
    """

    broker = Broker.get_instance()

    def __init__(self, broker=None):
        WebSocketServerProtocol.__init__(self)
        self._discovery_response_defer = None
        self._protocol_response_defer = None
        self._open_protocol_response_defer = None


    def onClose(self, wasClean, code, reason):
        print("Closing:" + str(self))
        # clean up after ourselves
        self.broker.adapters.remove(self)

    def send_message_as_JSON(self, msg):
        """
        Send a message dictionary as JSON
        """
        self.sendMessage(json.dumps(msg))

    def onMessage(self, payload, isBinary):
        if not isBinary:
            msg = json.loads(payload)

            # if we're waiting for discovery and its a discovery response
            if self._discovery_response_defer is not None and \
                    msg['TOPICS'].get('type', None) == 'get_protocol_discovery_response':
                # discovery!
                # get skeleton
                discovery = msg['CONTENTS'].get('discovery', [])
                self._discovery_response_defer.callback(discovery)
                self._discovery_response_defer = None
            # if we're waiting for a protocol list and its a protocol response
            elif self._protocol_response_defer is not None and \
                    msg['TOPICS'].get('type', None) == 'get_protocol_list_response':

                protocol_list = msg['CONTENTS'].get('protocol_list', [])
                self._protocol_response_defer.callback(protocol_list)
                self._protocol_response_defer = None


            # else its just a regular message, publish it.
            else:
                self.broker.publish(msg, self.send_message_as_JSON)

        else:
            print("Binary messages not supported")

    def onConnect(self, request):
        # let the broker know we exist!
        self.broker.adapters.append(self)

    def discover(self, force):
        # already in the middle of discovery
        if self._discovery_response_defer is not None:
            return self._discovery_response_defer

        self._discovery_response_defer = defer.Deferred()
        self.send_message_as_JSON({'TOPICS': {'type': 'get_protocol_discovery'}, 'CONTENTS': {}})

        def timeout():
            if self._discovery_response_defer is not None:
                # call back with nothing if timeout
                self._discovery_response_defer.callback({})
                self._discovery_response_defer = None

        self.broker.reactor.callLater(10, timeout)

        return self._discovery_response_defer

    def get_protocols(self):
        """
        Return a list of protocols that could potentially be opened.
        Return a deferred if this is not ready yet
        """
        # already in the middle of discovery
        if self._protocol_response_defer is not None:
            return self._protocol_response_defer

        self._protocol_response_defer = defer.Deferred()
        self.send_message_as_JSON({'TOPICS': {'type': 'get_protocol_list'}, 'CONTENTS': {}})

        def timeout():
            if self._protocol_response_defer is not None:
                # call back with nothing if timeout
                self._protocol_response_defer.callback({})
                self._protocol_response_defer = None

        self.broker.reactor.callLater(2, timeout)

        return self._protocol_response_defer

    def get_open_protocols(self):
        return []

    def __str__(self):
        return "Websocket at " + str(self.peer)


class WebsocketClientAdapter(Adapter, WebSocketClientProtocol):
    """
    Connect a Python item to the Broker over a Websocket
    """

    def __init__(self):
        WebSocketClientProtocol.__init__(self)
        Adapter.__init__(self)
        self._subscribe_q = []
        self._listener_list = []  # no way to unsubscribe. Subscriptions last

    def onConnect(self, request):
        WebSocketClientProtocol.onConnect(self, request)
        self._connected.callback(True)
        # flush our subscription requests
        for _fn, topics in self._subscribe_q:
            self.subscribe(_fn, **topics)
        self._subscribe_q = []  # empty the list

    def call_on_every_message(self, listener):
        self._listener_list.append(listener)

    def onMessage(self, packet, isBinary):
        """
        We got a message.  See who wants to process it.
        """
        if isBinary:
            print("WebsocketBrokerProtocol doesn't understand binary messages")
            return

        msg = json.loads(packet)
        # run it through the listeners for processing
        for fn in self._listener_list:
            fn(msg)

    def subscribe(self, _fn=None, **topics):
        """
        Subscribe to messages the topics in **kwargs
        """
        # wait until we're connected to subscribe
        if not self.connected:
            self._subscribe_q.append((_fn, topics))
            return

        self.publish({"TOPICS": {'type': 'subscribe'}, "CONTENTS": {'TOPICS': topics}})
        if _fn is not None:
            def listener(msg):
                t = msg["TOPICS"]
                if all(k in t and v == t[k] for k, v in t.items()):
                    _fn(msg)

            self._listener_list.append(listener)

    def publish(self, msg, callback=None):
        if not self.connected:
            raise RuntimeError("Not Connected to Broker yet")
        self.sendMessage(json.dumps(msg))


class WebsocketClientAdapterFactory(WebSocketClientFactory):
    def __init__(self, *args, **kwargs):
        self.adapter = WebsocketClientAdapter()  # this is the adapter singleton
        WebSocketClientFactory.__init__(self, *args, **kwargs)

    def buildProtocol(self, addr):
        adapter = self.adapter
        adapter.factory = self

        return adapter
