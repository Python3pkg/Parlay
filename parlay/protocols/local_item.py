"""
The Local Item protocol lets you open arbitrary items that have been registered as local
"""

from .base_protocol import BaseProtocol
from parlay.server.broker import Broker
from functools import wraps

LOCAL_ITEM_CLASSES = {}


def local_item(auto_connect=False):
    """
    A class decorator for python Items that are not part of an external protocol.

    Local items are self-contained, and do not communicate over external protocols, for
    example a serial port.  They are typically used for simulators or pure python computation
    items.

    :param auto_connect: whether to automatically connect to the Parlay broker when the item is created.
    :return: decorator function

    **Example usage of local_item decorator**::

        # motor_sim.py

        @local_item()
        class MotorSimulator(ParlayCommandItem):

            def __init__(self, item_id, item_name):
                ...

    **Example usage of defined local item**::

        import parlay
        from motor_sim import MotorSimulator

        MotorSimulator("motor1", "motor 1")  # motor1 will be discoverable
        parlay.start()

    """
    def decorator(cls):
        """
        Monkey-patch __init__ to open a protocol when an object is constructed
        """
        # register class with dict of local items
        class_name = cls.__name__
        cls._local_item_auto_connect = auto_connect  # set the auto connect flag
        LOCAL_ITEM_CLASSES[class_name] = cls
        # override __init__
        orig_init = cls.__init__

        @wraps(orig_init)
        def new_init(self, *args, **kwargs):
            """
            Call the original ctor and then pass self to a new local protocol and append it to the broker
            """
            result = orig_init(self, *args, **kwargs)
            protocol_obj = LocalItemProtocol(self)
            Broker.get_instance().pyadapter.track_open_protocol(protocol_obj)
            self._local_protocol = protocol_obj
            return result
        cls.__init__ = new_init
        cls.__orig_init__ = orig_init
        return cls

    return decorator


class LocalItemProtocol(BaseProtocol):
    ID = 0  # id counter for uniqueness

    class TransportStub(object):
        """
        Fake transport that will allow the protocol to think its writing to a transport
        """
        def __init__(self):
            self._broker = Broker.get_instance()

        def write(self, payload):
            self._broker.publish(payload)

    @classmethod
    def open(cls, broker, item_name):
        item_class = LOCAL_ITEM_CLASSES[item_name]
        obj = item_class()
        return obj._local_protocol



    @classmethod
    def open_for_obj(cls, item_obj):
        protocol_obj = LocalItemProtocol(item_obj)
        Broker.get_instance().pyadapter.track_open_protocol(protocol_obj)
        return protocol_obj

    @classmethod
    def get_open_params_defaults(cls):
        return {"item_name": list(LOCAL_ITEM_CLASSES.keys())}

    @classmethod
    def close(cls):
        pass  # Don't need to do anything

    def __init__(self, item):
        BaseProtocol.__init__(self)
        self.items = [item]  # only 1
        self._unique_id = LocalItemProtocol.ID
        self._broker = Broker.get_instance()
        LocalItemProtocol.ID += 1

    def __str__(self):
        return "Local:" + str(self.items[0].__class__) + " # " + str(self._unique_id)

auto_started_items = []

def auto_start():
    """
    Auto start local items that have that flag set
    """
    for name, cls in LOCAL_ITEM_CLASSES.items():
        if cls._local_item_auto_connect:
            #construct them on init and store them in the list so they don't get garbage collected
            auto_started_items.append(cls())

# call this when the Broker is up and running
Broker.call_on_start(auto_start)
