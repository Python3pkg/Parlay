import queue
import datetime
from parlay.items.threaded_item import ITEM_PROXIES, ThreadedItem, ListenerStatus
from parlay.items.base import MSG_STATUS, MSG_TYPES
from twisted.internet import defer
from twisted.python import failure
from parlay.server.broker import run_in_broker, run_in_thread


class ParlayStandardScriptProxy(object):
    """
    A proxy class for the script to use, that will auto-detect discovery information and allow script writers to
    intuitively use the item
    """

    class PropertyProxy(object):
        """
        Proxy class for a parlay property
        """

        def __init__(self, id, item_proxy, blocking_set=True):
            self._id = id
            self._item_proxy = item_proxy
            # do we want to block on a set until we get the ACK?
            self._blocking_set = blocking_set

        def __get__(self, instance, owner):
            msg = instance._script.make_msg(instance.item_id, None, msg_type=MSG_TYPES.PROPERTY,
                                            direct=True, response_req=True, PROPERTY=self._id, ACTION="GET")
            resp = instance._script.send_parlay_message(msg)
            # return the VALUE of the response
            return resp["CONTENTS"]["VALUE"]

        def __set__(self, instance, value):
            try:
                msg = instance._script.make_msg(instance.item_id, None, msg_type=MSG_TYPES.PROPERTY,
                                            direct=True, response_req=self._blocking_set,
                                            PROPERTY=self._id, ACTION="SET", VALUE=value)
                # Wait until we're sure its set
                resp = instance._script.send_parlay_message(msg)
            except TypeError as e:
                print("Could not set property to non JSON serializable type. You tried to set", self._id, "to", value)

            except Exception as e:
                print("Caught general exception while trying to set", self._id, "to", value)

        def __str__(self):
            return str(self.__get__(self._item_proxy, self._item_proxy))

    class StreamProxy(object):
        """
        Proxy class for a parlay stream
        """

        MAX_LOG_SIZE = 1000000

        def __init__(self, id, item_proxy, rate):
            self._id = id
            self._item_proxy = item_proxy
            self._val = None
            self._rate = rate
            self._listener = lambda _: _
            self._new_value = defer.Deferred()
            self._reactor = self._item_proxy._script._reactor
            self._subscribed = False
            self._is_logging = False
            self._log = []

            item_proxy._script.add_listener(self._update_val_listener)

        def attach_listener(self, listener):
            self._listener = listener

        def wait_for_value(self):
            """
            If in thread:
                Will block until datastream is updated
            If in Broker:
                Will return deferred that is called back with the datastream value when updated
            """
            self.get()
            return self._reactor.maybeblockingCallFromThread(lambda: self._new_value)

        def get(self):
            if not self._subscribed:
                msg = self._item_proxy._script.make_msg(self._item_proxy.item_id, None, msg_type=MSG_TYPES.STREAM,
                                                        direct=True, response_req=False, STREAM=self._id, STOP=False,
                                                        RATE=self._rate)

                self._item_proxy._script.send_parlay_message(msg)
                self._subscribed = True
            return self._val

        def stop(self):
            """
            Stop streaming
            :return:
            """
            msg = self._item_proxy._script.make_msg(self._item_proxy.item_id, None, msg_type=MSG_TYPES.STREAM,
                                                    direct=True, response_req=False, STREAM=self._id, STOP=True)

            self._item_proxy._script.send_parlay_message(msg)
            self._subscribed = False

        def _update_val_listener(self, msg):
            """
            Script listener that will update the val whenever we get a stream update
            """
            topics, contents = msg["TOPICS"], msg['CONTENTS']
            if topics.get("MSG_TYPE", "") == MSG_TYPES.STREAM and topics.get("STREAM", "") == self._id \
                    and 'VALUE' in contents:
                new_val = contents["VALUE"]
                if self._is_logging:
                    self._add_to_log(new_val)
                self._listener(new_val)
                self._val = new_val
                temp = self._new_value
                self._new_value = defer.Deferred() # set up a new one
                temp.callback(new_val)
            return ListenerStatus.KEEP_LISTENER

        def _add_to_log(self, update_val):
            """
            Helper function for adding the latest val to the log list
            """
            if len(self._log) >= self.MAX_LOG_SIZE:  # handle overflow
                # NOTE: pop() then append() is faster than list[1:].append()
                self._log.pop(0)

            self._log.append(self._create_data_entry(update_val))  # if adequate list size, append normally

        def start_logging(self, rate):
            """
            Script function that enables logging. When a new value is pushed to the datastream
            the value will get pushed to the end of the log.

            """
            self._rate = rate
            self.get()
            self._is_logging = True

        def stop_logging(self):
            """
            Script function that disables logging.
            """
            self.stop()
            self._is_logging = False  # reset logging variable

        def clear_log(self):
            """
            Resets the internal log
            """

            self._log = []

        def get_log(self):
            """
            Public interface to get the stream log
            """
            return self._log

        @staticmethod
        def _create_data_entry(update_val):
            """
            Creates a data entry with val and timestamp
            """

            return datetime.datetime.now(), update_val

    def __init__(self, discovery, script):
        """
        discovery: The discovery dictionary for the item that we're proxying
        script: The script object we're running in
        """
        self.name = discovery["NAME"]
        self.item_id = discovery["ID"]
        self._discovery = discovery
        self._script = script
        self.datastream_update_rate_hz = 2
        self.timeout = 120
        self._command_id_lookup = {}

        # look at the discovery and add all commands, properties, and streams

        # commands
        func_dict = next(iter([x for x in discovery['CONTENT_FIELDS'] if x['MSG_KEY'] == 'COMMAND']), None)

        if func_dict is not None:  # if we have commands
            command_names = [x[0] for x in func_dict["DROPDOWN_OPTIONS"]]
            command_ids = [x[1] for x in func_dict["DROPDOWN_OPTIONS"]]
            command_args = [x for x in func_dict["DROPDOWN_SUB_FIELDS"]]

            for i in range(len(command_names)):
                func_name = command_names[i]
                func_id = command_ids[i]
                self._command_id_lookup[func_name] = func_id  # add to lookup for fast access later
                arg_names = [(x['MSG_KEY'], x.get('DEFAULT', None)) for x in command_args[i]]

                def _closure_wrapper(f_name=func_name, f_id=func_id, _arg_names=arg_names, _self=self):

                    @run_in_broker
                    @defer.inlineCallbacks
                    def func(*args, **kwargs):
                        if len(args) + len(kwargs) > len(_arg_names):
                            raise KeyError("Too many Arguments. Expected arguments are: " +
                                           str([str(x[0]) for x in _arg_names]))
                        # add positional args with name lookup
                        for j in range(len(args)):
                            kwargs[_arg_names[j][0]] = args[j]

                        # check args
                        for name, default in _arg_names:
                            if name not in kwargs and default is None:
                                raise TypeError("Missing argument: "+name)

                        # send the message and block for response
                        msg = _self._script.make_msg(_self.item_id, f_id, msg_type=MSG_TYPES.COMMAND,
                                                     direct=True, response_req=True, COMMAND=f_name, **kwargs)

                        resp = yield _self._script.send_parlay_message(msg, timeout=_self.timeout)
                        yield defer.returnValue(resp['CONTENTS'].get('RESULT', None))

                    # set this object's function to be that function
                    setattr(_self, f_name, func)

                # need this trickery so closures work in a loop
                _closure_wrapper()

        self.streams = {}
        # streams
        for stream in discovery.get("DATASTREAMS", []):
            stream_id = stream["STREAM"]
            stream_name = stream["STREAM_NAME"] if "STREAM_NAME" in stream else stream_id
            self.streams[stream_name] = ParlayStandardScriptProxy.StreamProxy(stream_id, self,
                                                                              self.datastream_update_rate_hz)
        # properties
        for prop in discovery.get("PROPERTIES", []):
            property_id = prop["PROPERTY"]
            property_name = prop["PROPERTY_NAME"] if "PROPERTY_NAME" in prop else property_id
            setattr(self, property_name, ParlayStandardScriptProxy.PropertyProxy(property_id, self))



    def send_parlay_command(self, command, **kwargs):
        """
        Manually send a parlay command. Returns a handle that can be paused on
        """
        # send the message and block for response
        msg = self._script.make_msg(self.item_id, self._command_id_lookup[command], msg_type=MSG_TYPES.COMMAND,
                                    direct=True, response_req=True, COMMAND=command, **kwargs)
        # make the handle that sets up the listeners
        handle = CommandHandle(msg, self._script)
        self._script.send_parlay_message(msg, timeout=self.timeout, wait=False)
        return handle

    def get_datastream_handle(self, name):
        return object.__getattribute__(self, name)

    # Some re-implementation so our instance-bound descriptors will work instead of having to be class-bound.
    # Thanks: http://blog.brianbeck.com/post/74086029/instance-descriptors
    def __getattribute__(self, name):
        value = object.__getattribute__(self, name)
        if isinstance(value, ParlayStandardScriptProxy.PropertyProxy):
            value = value.__get__(self, self.__class__)
        return value

    def __setattr__(self, name, value):
        try:
            obj = object.__getattribute__(self, name)
        except AttributeError:
            pass
        else:
            if isinstance(obj, ParlayStandardScriptProxy.PropertyProxy):
                return obj.__set__(self, value)
        return object.__setattr__(self, name, value)



# register the proxy so it can be used in scripts
ITEM_PROXIES['ParlayStandardItem'] = ParlayStandardScriptProxy
ITEM_PROXIES['ParlayCommandItem'] = ParlayStandardScriptProxy


class CommandHandle(object):
    """
    This is a command handle that wraps a command message and allows blocking until certain messages are recieved
    """

    def __init__(self, msg, script):
        """
        :param msg the message that we're handling
        :param script the script context that we're in
        """
        topics, contents = msg["TOPICS"], msg["CONTENTS"]
        assert 'MSG_ID' in topics and 'TO' in topics and 'FROM' in topics
        self._msg = msg
        self._msg_topics = topics
        self._msg_content = contents
        # :type ParlayScript
        self._script = script
        self.msg_list = []  # list of al messages with the same message id but swapped TO and FROM
        self._done = False  # True when we're done listening (So we can clean up)
        self._queue = queue.Queue()

        # add our listener
        self._script.add_listener(self._generic_on_message)

    def _generic_on_message(self, msg):
        """
        Listener function that powers the handle.
        This should only be called by a script in the reactor thread in its listener loop
        """

        topics, contents = msg["TOPICS"], msg["CONTENTS"]
        if topics.get("MSG_ID", None) == self._msg_topics["MSG_ID"] \
                and topics.get("TO", None) == self._msg_topics["FROM"] \
                and topics.get("FROM", None) == self._msg_topics['TO']:

            # add it to the list if the msg ids match but to and from are swapped (this is for inspection later)
            self.msg_list.append(msg)
            # add it to the message queue for messages that we have not looked at yet
            self._queue.put_nowait(msg)

            status = topics.get("MSG_STATUS", None)
            msg_type = topics.get("MSG_TYPE", None)
            if msg_type == MSG_TYPES.RESPONSE and status != MSG_STATUS.PROGRESS:
                #  if it's a response but not an ack, then we're done
                self._done = True

        # remove this function from the listeners list
        return self._done

    @run_in_thread
    def wait_for(self, fn, timeout=None):
        """
        Block and wait for a message in our queue where fn returns true. Return that message
        """
        msg = self._queue.get(timeout=timeout, block=True)
        while not fn(msg):
            msg = self._queue.get(timeout=timeout, block=True)

        return msg

    @run_in_thread
    def wait_for_complete(self):
        """
        Called from a scripts thread. Blocks until the message is complete.
        """

        msg = self.wait_for(lambda msg: msg["TOPICS"].get("MSG_STATUS",None) != MSG_STATUS.PROGRESS and
                                        msg["TOPICS"].get("MSG_TYPE", None) == MSG_TYPES.RESPONSE)

        # if the  status is OK, then get the result, optherwise get the description
        status = msg["TOPICS"].get("MSG_STATUS", None)
        if status == MSG_STATUS.OK:
            return msg["CONTENTS"].get("RESULT", msg["CONTENTS"])
        elif status == MSG_STATUS.ERROR:
            raise BadStatusError("Error returned from item", msg["CONTENTS"].get("DESCRIPTION", ""))

    @run_in_thread
    def wait_for_ack(self):
        """
        Called from a scripts thread. Blocks until the message is ackd
        """
        msg = self.wait_for(lambda msg: msg["TOPICS"].get("MSG_STATUS",None) == MSG_STATUS.PROGRESS and
                                        msg["TOPICS"].get("MSG_TYPE", None) == MSG_TYPES.RESPONSE)

        return msg


class BadStatusError(Exception):
    """
    Throw this if you want to return a Bad Status!
    """
    def __init__(self, error, description=""):
        self.error = error
        self.description = description

    def __str__(self):
        return str(self.error) + "\n" + str(self.description)