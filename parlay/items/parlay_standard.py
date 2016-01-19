import functools
from base import BaseItem
from parlay.protocols.utils import message_id_generator
from twisted.internet import defer, threads
from twisted.python import failure
from parlay.server.broker import Broker, run_in_broker, run_in_thread
from twisted.internet.task import LoopingCall
from parlay.items.threaded_item import ITEM_PROXIES, ThreadedItem
from parlay.items.base import INPUT_TYPES, MSG_STATUS, MSG_TYPES, TX_TYPES, INPUT_TYPE_DISCOVERY_LOOKUP, INPUT_TYPE_CONVERTER_LOOKUP
import json
import re

class ParlayStandardItem(ThreadedItem):
    """
    This is a parlay standard item. It supports building inputs for the UI in an intuitive manner during
    discovery. Inherit from it and use the parlay decorators to get UI functionality
    """

    def __init__(self, item_id, name):
        # call parent
        ThreadedItem.__init__(self, item_id, name)
        self._content_fields = []
        self._topic_fields = []
        self._properties = {}  # Dictionary from name to (attr_name, read_only, write_only)
        self._datastreams = {}
        self.item_type = None
        self._msg_id_generator = message_id_generator(65535, 100) # default msg ids are 32 bit ints between 100 and 65535



    def create_field(self,  msg_key, input, label=None, required=False, hidden=False, default=None,
                  dropdown_options=None, dropdown_sub_fields=None):
        """
        Create a field for the UI
        """
        # make sure input is valid
        assert input in INPUT_TYPES.__dict__

        discovery = {"MSG_KEY": msg_key, "INPUT": input, "REQUIRED": required, "HIDDEN": hidden}

        if label is not None: discovery["LABEL"] = label
        if default is not None: discovery["DEFAULT"] = default
        if input == INPUT_TYPES.DROPDOWN:
            discovery["DROPDOWN_OPTIONS"] = dropdown_options
            discovery["DROPDOWN_SUB_FIELDS"] = dropdown_sub_fields

        return discovery

    def add_field(self,  msg_key, input, label=None, required=False, hidden=False, default=None,
                  dropdown_options=None, dropdown_sub_fields=None, topic_field=False):
        """
        Add a field to this items discovery.
        This field will show up in the item's CARD in the UI
        """

        discovery = self.create_field(msg_key, input, label, required, hidden, default, dropdown_options,
                                      dropdown_sub_fields)

        if topic_field:
            self._topic_fields.append(discovery)
        else:
            self._content_fields.append(discovery)

    def add_property(self, name, attr_name=None, input=INPUT_TYPES.STRING, read_only=False, write_only=False):
        """
        Add a property to this Item.
        name : The name of the property
        attr_name = the name of the attr to set in 'self' when setting and getting (None if same as name)
        read_only = Read only
        write_only = write_only
        """
        attr_name = attr_name if attr_name is not None else name  # default
                                                # attr_name isn't needed for discovery, but for lookup
        self._properties[name] = {"NAME": name, "ATTR_NAME": attr_name, "INPUT": input,
                                  "READ_ONLY": read_only, "WRITE_ONLY": write_only}  # add to internal list

    def add_datastream(self, name, attr_name=None, units=""):
        """
        Add a property to this Item.
        name : The name of the property
        attr_name = the name of the attr to set in 'self' when setting and getting (None if same as name)
        read_only = Read only
        write_only = write_only
        """
        attr_name = attr_name if attr_name is not None else name  # default
                                                # attr_name isn't needed for discovery, but for lookup
        self._datastreams[name] = {"NAME": name, "ATTR_NAME": attr_name, "UNITS": units}  # add to internal list



    def clear_fields(self):
        """
        clears all fields. Useful to change the discovery UI
        """
        del self._topic_fields[:]
        del self._content_fields[:]

    def get_discovery(self):
        """
        Discovery method. You can override this in a subclass if you want, but it will probably be easier to use the
        self.add_property and self.add_field helper methods and call this method like:
        def get_discovery(self):
            discovery = ParlayStandardItem.get_discovery(self)
            # do other stuff for subclass here
            return discovery
        """

        #get from parent
        discovery = BaseItem.get_discovery(self)
        discovery["TOPIC_FIELDS"] = self._topic_fields
        discovery["CONTENT_FIELDS"] = self._content_fields
        discovery["PROPERTIES"] = [x for x in self._properties.values()]  # already formatted correctly.
        discovery["DATASTREAMS"] = [x for x in self._datastreams.values()]  # already .formatted correctly.

        if self.item_type is not None:
            discovery["TYPE"] = self.item_type

        return discovery

    def send_message(self, to, from_=None, contents=None, tx_type=TX_TYPES.DIRECT, msg_type=MSG_TYPES.DATA, msg_id=None,
                     msg_status=MSG_STATUS.OK, response_req=False, extra_topics=None):
        """
        Sends a Parlay standard message.
        contents is a dictionary of contents to send
        """
        if msg_id is None:
            msg_id = self._msg_id_generator.next()
        if contents is None:
            contents = {}
        if from_ is None:
            from_ = self.item_id

        msg = {"TOPICS": {"TO": to, "FROM": from_, "TX_TYPE": tx_type, "MSG_TYPE": msg_type, "MSG_ID": msg_id,
                          "MSG_STATUS": msg_status, "RESPONSE_REQ": response_req},
               "CONTENTS": contents}

        if extra_topics is not None:
            msg["TOPICS"].update(extra_topics)

        self._broker.publish(msg, self.on_message)





def parlay_command(async=False, auto_type_cast=True):
    """
    Make the decorated method a parlay_command.

    :param: async: If True, will run as a normal twisted async function call. If False, parlay will spawn a separate
    thread and run the function synchronously (Default false)
    """

    def decorator(fn):
        if async:
            wrapper = run_in_broker(fn)
        else:
            wrapper = run_in_thread(fn)

        wrapper._parlay_command = True
        wrapper._parlay_fn = fn  # in case it gets wrapped again, this is the actual function so we can pull kwarg names
        wrapper._parlay_arg_conversions = {}  # if type casting desired, this dict from param_types to converting funcs
        wrapper._parlay_arg_discovery = {}

        if auto_type_cast and fn.__doc__ is not None:
            for line in fn.__doc__.split("\n"):
                m = re.search(r"[@:]type\s+(\w+)\s*[ :]\s*(\w+\[?\w*\]?)", line)
                if m is not None:
                    arg_name, arg_type = m.groups()
                    if arg_type in INPUT_TYPE_CONVERTER_LOOKUP:  # if we know how to convert it
                        wrapper._parlay_arg_conversions[arg_name] = INPUT_TYPE_CONVERTER_LOOKUP[arg_type] # add to convert list
                        wrapper._parlay_arg_discovery[arg_name] = INPUT_TYPE_DISCOVERY_LOOKUP.get(arg_type, INPUT_TYPES.STRING)

        return wrapper

    return decorator

class parlay_property(object):
    """
    A Property convenience class for ParlayCommandItems.
    In the item use like: self.prop = parlay_property()

    init_val : an inital value for the property
    val_type : the python type of the value. e.g. str, int, list, etc. The value will be coerced to this type on set
                and throw an exception if it couldn't be coerced
    read_only: Set to true to make read only
    write_only: Set to true to make write only
    """

    def __init__(self, default=None, val_type=None, read_only=False, write_only=True):
        self._val_lookup = {}  # lookup based on instance
        self._init_val = default
        self._read_only = read_only
        self._write_only = write_only
        self._val_type = val_type
        # can't be both read and write only
        assert(not(self._read_only and write_only))


    def __get__(self, instance, objtype=None):
        return self._val_lookup.get(instance, self._init_val)

    def __set__(self, instance, value):
        # coerce the val
        val = value if self._val_type is None else self._val_type(value)
        self._val_lookup[instance] = val

class parlay_datastream(object):
    """
    A DataStream convenience class for ParlayCommandItems.
    In the item use like: self.prop = parlay_datastream()
    Datastream are read-only values that alert listeners  at a certain frequency

    init_val : an inital value for the property
    val_type : the python type of the value. e.g. str, int, list, etc. The value will be coerced to this type on set
                and throw an exception if it couldn't be coerced
    read_only: Set to true to make read only
    write_only: Set to true to make write only
    """

    def __init__(self, default=None, units=""):
        self._default_val = default
        self.listeners = {}  # key -> (key -> value) | requester -> (instance -> repeater)
        self.units = units
        self.broker = Broker.get_instance()
        self._vals = {}  # key -> value | instance -> value

    def __get__(self, instance, objtype=None):
        return self._vals.get(instance, self._default_val)

    def __set__(self, instance, value):
        self._vals[instance] = value

    def stream(self, instance, requester_id, looper, hz):
        current_looper = self.listeners.get(requester_id, {}).get(instance, None)
        if current_looper is not None and current_looper.running:
            current_looper.stop()
        if hz > 0:
            #setup dict if its first time
            if requester_id not in self.listeners:
                self.listeners[requester_id] = {}
            self.listeners[requester_id][instance] = looper
            looper.start(1/hz)


class BadStatusError(Exception):
    """
    Throw this if you want to return a Bad Status!
    """
    def __init__(self, error, description=""):
        self.error = error
        self.description = description
                                                    #inherit from script for easy command access
class ParlayCommandItem(ParlayStandardItem):
    """
    This is a parlay item that takes commands, with values.
    If there is more than one command A dropdown will appear
    """
    #id generator for auto numbering class instances

    __ID_GEN = message_id_generator(2^32, 1)

    def __init__(self, item_id=None, name=None):
        # call parent
        """

        :rtype : object
        """
        if item_id is None:
            item_id = self.__class__.__name__ + " " + str(ParlayCommandItem.__ID_GEN.next())

        if name is None:
            name = self.__class__.__name__

        ParlayStandardItem.__init__(self, item_id, name)
        self._commands = {}  # dict with command name -> callback function

        #ease of use deferred for wait* functions
        self._wait_for_next_sent_message = defer.Deferred()
        self._wait_for_next_recv_message = defer.Deferred()

        self._broker.subscribe(self._wait_for_next_recv_msg_subscriber, TO=self.item_id)
        self._broker.subscribe(self._wait_for_next_sent_msg_subscriber, FROM=self.item_id)

        #add any function that have been decorated
        for member_name in [x for x in dir(self) if not x.startswith("__")]:
            member = getattr(self, member_name, {})
            # are we a method? and do we have the flag, and is it true?
            if callable(member) and hasattr(member, "_parlay_command") and member._parlay_command:
                self._commands[member_name] = member
                #build the sub-field based on their signature
                arg_names = member._parlay_fn.func_code.co_varnames[1:member._parlay_fn.func_code.co_argcount]   # remove self
                # TODO: defaults based on method signature
                #add the sub_fields, trying to best guess their discovery types. If not possible then default to STRING
                member.__func__._parlay_sub_fields = [self.create_field(x, member._parlay_arg_discovery.get(x, INPUT_TYPES.STRING)) for x in arg_names]


        #run discovery to init everything for a first time
        #call it immediately after init
        self._broker._reactor.callLater(0, ParlayCommandItem.get_discovery, self)


    def get_discovery(self):
        """
        Will auto-populate the UI with inputs for commands
        """
        #start fresh
        self.clear_fields()
        self._add_commands_to_discovery()
        self._add_properties_to_discovery()
        self._add_datastreams_to_discovery()

        #call parent
        return ParlayStandardItem.get_discovery(self)

    def _add_commands_to_discovery(self):
        """
        Add commands to the discovery for user input
        """

        if len(self._commands) == 0:
            return  # nothing to do here
        if len(self._commands) == 1:
            # If only one, then drop the dropdown option and have a hidden text option
            func_name=self._commands.keys()[0]
            func = self._commands[func_name]
            self.add_field("COMMAND", INPUT_TYPES.STRING, default=func_name, hidden=True)
            #add the arguments as strait input fields
            self._content_fields.extend(func._parlay_sub_fields)

        else:  # more than 1 option
            command_names = self._commands.keys()
            command_names.sort()  # pretty-sort

            # add the command selection dropdown
            self.add_field("COMMAND", INPUT_TYPES.DROPDOWN, label='command', default=command_names[0],
                           dropdown_options=[(x, x) for x in command_names],
                           dropdown_sub_fields=[self._commands[x]._parlay_sub_fields for x in command_names]
                           )

    def _add_properties_to_discovery(self):
        """
        Add properties to discovery
        """
        #clear properties
        self._properties = {}
        for member_name in [x for x in dir(self.__class__) if not x.startswith("__")]:
            member = self.__class__.__dict__.get(member_name, None)
            if isinstance(member, parlay_property):
                self.add_property(member_name, member_name, INPUT_TYPES.STRING,
                                  read_only=member._read_only, write_only=member._write_only)

    def _add_datastreams_to_discovery(self):
        """
        Add properties to discovery
        """
        #clear properties
        self._datastreams = {}
        for member_name in [x for x in dir(self.__class__) if not x.startswith("__")]:
            member = self.__class__.__dict__.get(member_name, None)
            if isinstance(member, parlay_datastream):
                self.add_datastream(member_name, member_name, member.units)


    def _send_parlay_message(self, msg):
        self._broker.publish(msg, self.on_message)


    def on_message(self, msg):
        """
        Will handle command messages automatically.
        Returns True if the message was handled, False otherwise
        """
        # run it through the listeners for processing
        self._runListeners(msg)

        topics, contents = msg["TOPICS"], msg["CONTENTS"]
        msg_type = topics.get("MSG_TYPE", "")
        #handle property messages
        if msg_type == "PROPERTY":
            action = contents.get('ACTION', "")
            property_name = str(contents.get('PROPERTY', ""))
            try:
                if action == 'SET':
                    assert 'VALUE' in contents  # we need a value to set!
                    setattr(self, self._properties[property_name]["ATTR_NAME"], contents['VALUE'])
                    self.send_response(msg, {"PROPERTY": property_name, "ACTION": "RESPONSE"})
                    return True
                elif action == "GET":
                    val = getattr(self, self._properties[property_name]["ATTR_NAME"])
                    self.send_response(msg, {"PROPERTY": property_name, "ACTION": "RESPONSE", "VALUE": val})
                    return True
            except Exception as e:
                self.send_response(msg, {"PROPERTY": property_name, "ACTION": "RESPONSE", "DESCRIPTION": str(e)},
                                   msg_status=MSG_STATUS.ERROR)


        #handle data stream messages
        if msg_type == "STREAM":
            try:
                stream_name = str(contents["STREAM"])
                hz = float(contents["RATE"])
                requester = topics["FROM"]
                def sample():
                    val = getattr(self, self._datastreams[stream_name]["ATTR_NAME"])
                    self.send_message(to=requester, msg_type=MSG_TYPES.STREAM, contents={'VALUE': val},
                                      extra_topics={"STREAM": stream_name})

                looper = LoopingCall(sample)
                self.__class__.__dict__[stream_name].stream(self, requester, looper, hz)

            except Exception as e:
                self.send_response(msg, {"STREAM": contents.get("STREAM", "__UNKNOWN_STREAM__"), "ACTION": "RESPONSE", "DESCRIPTION": str(e)},
                                   msg_status=MSG_STATUS.ERROR)
        #handle 'command' messages
        command = contents.get("COMMAND", "")
        if command in self._commands:
            arg_names = ()
            try:
                method = self._commands[command]
                arg_names = method._parlay_fn.func_code.co_varnames[1:method._parlay_fn.func_code.co_argcount]  # remove 'self'
                kws = {k: msg["CONTENTS"][k] for k in arg_names}
                try:
                    #do any type conversions (deault to whatever we were sent if no conversions
                    kws = {k: method._parlay_arg_conversions[k](v) if k in method._parlay_arg_conversions else v for  k,v in kws.iteritems()}
                except ValueError as e:
                    self.send_response(msg, contents={"DESCRIPTION": e.message, "ERROR": "BAD TYPE"},
                                           msg_status=MSG_STATUS.ERROR)
                    return None
                # try to run the method, return the data and say status ok
                def run_command():
                    return method(**kws)

                result = defer.maybeDeferred(run_command)
                result.addCallback(lambda r: self.send_response(msg, {"RESULT": r}))
                def bad_status_errback(f):
                    #is this an exppliti bad status?
                    if isinstance(f.value, BadStatusError):
                        error = f.value
                        self.send_response(msg, contents={"DESCRIPTION": error.description, "ERROR": error.error},
                                           msg_status=MSG_STATUS.ERROR)
                    #or is it unknown generic exception?
                    else:
                        self.send_response(msg, contents={"DESCRIPTION": f.getErrorMessage(), "TRACEBACK": f.getTraceback()},
                                           msg_status=MSG_STATUS.ERROR)

                #if we get an error, then return it
                result.addErrback(bad_status_errback)

            except KeyError as e:
                # TODO: Reply with human readible error message for UI
                print("ERROR: missing args" + str(arg_names))

            return True
        else:
            return False




    def _wait_for_next_sent_msg_subscriber(self, msg):
        d = self._wait_for_next_sent_message
        #set up new one before calling callback in case things triggered by the callback need to wait for the next sent
        self._wait_for_next_sent_message = defer.Deferred()
        d.callback(msg)

    def _wait_for_next_recv_msg_subscriber(self, msg):
        d = self._wait_for_next_recv_message
        #set up new one before calling callback in case things triggered by the callback need to wait for the next sent
        self._wait_for_next_recv_message = defer.Deferred()
        d.callback(msg)

    def wait_for_next_sent_msg(self):
        """
        Returns a deferred that will callback on the next message we SEND
        """
        return self._wait_for_next_sent_message

    def wait_for_next_recv_msg(self):
        """
        Returns a deferred that will callback on the next message we RECEIVE
        """
        return self._wait_for_next_recv_message

    def send_response(self, msg, contents=None, msg_status=MSG_STATUS.OK):
        if contents is None:
            contents = {}

        #swap to and from
        to, from_ = msg["TOPICS"]["FROM"], msg["TOPICS"]['TO']
        self.send_message(to, from_, tx_type=TX_TYPES.DIRECT, msg_type=MSG_TYPES.RESPONSE,
                          msg_id=msg["TOPICS"]["MSG_ID"], msg_status=msg_status, response_req=False,
                          contents=contents)




class ParlayStandardScriptProxy(object):
    """
    A proxy class for the script to use, that will auto-detect discovery information and allow script writers to
    intuitively use the item
    """

    class PropertyProxy(object):
        """
        Proxy class for a parlay property
        """

        def __init__(self, name, item_proxy, blocking_set=True):
            self._name = name
            self._item_proxy = item_proxy
            # do we want to block on a set until we get the ACK?
            self._blocking_set = blocking_set

        def __get__(self, instance, owner):
            msg = instance._script.make_msg(instance.name, None, msg_type=MSG_TYPES.PROPERTY,
                                            direct=True, response_req=True, PROPERTY=self._name, ACTION="GET")
            resp = instance._script.send_parlay_message(msg)
            #reutn the VALUE of the response
            return resp["CONTENTS"]["VALUE"]

        def __set__(self, instance, value):
            msg = instance._script.make_msg(instance.name, None, msg_type=MSG_TYPES.PROPERTY,
                                            direct=True, response_req=self._blocking_set,
                                            PROPERTY=self._name, ACTION="SET", VALUE=value)
            #Wait until we're sure its set
            resp = instance._script.send_parlay_message(msg)

        def __str__(self):
            return str(self.__get__(self._item_proxy, self._item_proxy))


    class StreamProxy(object):
        """
        Proxy class for a parlay stream
        """

        def __init__(self, name, item_proxy, rate):
            self._name = name
            self._item_proxy = item_proxy
            self._val = None
            self._rate = rate

            item_proxy._script.add_listener(self._update_val_listener)

            msg = item_proxy._script.make_msg(item_proxy.name, None, msg_type=MSG_TYPES.STREAM,
                                            direct=True, response_req=False, STREAM=self._name, RATE=rate)
            item_proxy._script.send_parlay_message(msg)

        def _update_val_listener(self, msg):
            """
            Script listener that will update the val whenever we get a stream update
            """
            topics, contents = msg["TOPICS"], msg['CONTENTS']
            if topics.get("MSG_TYPE", "") == MSG_TYPES.STREAM and topics.get("STREAM", "") == self._name \
                    and 'VALUE' in contents:
                self._val = contents["VALUE"]
            return False  # never eat me!

        def __get__(self, instance, owner):
            return self._val

        def __set__(self, instance, value):
            raise NotImplementedError()  # you can't set a STREAM



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
        self.timeout = 30
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
                def _closure_wrapper(func_name=func_name, func_id=func_id, arg_names=arg_names, self=self):
                    def func(*args, **kwargs):
                        #ad positional args with name lookup
                        for j in range(len(args)):
                            kwargs[arg_names[j][0]] = args[j]

                        # check args
                        for name, default in arg_names:
                            if name not in kwargs and default is None:
                                raise TypeError("Missing argument: "+name)

                        #send the message and block for response
                        msg = self._script.make_msg(self.item_id, func_id, msg_type=MSG_TYPES.COMMAND,
                                                direct=True, response_req=True, COMMAND=func_name, **kwargs)
                        resp = self._script.send_parlay_message(msg, timeout=self.timeout)
                        return resp['CONTENTS'].get('RESULT', None)

                    # set this object's function to be that function
                    setattr(self, func_name, func)
                #need this trickery so closures work in a loop
                _closure_wrapper()

        #properties
        for prop in discovery.get("PROPERTIES", []):
            setattr(self, prop['NAME'], ParlayStandardScriptProxy.PropertyProxy(prop['NAME'], self))

        #stream
        for stream in discovery.get("DATASTREAMS", []):
            setattr(self, stream['NAME'], ParlayStandardScriptProxy.StreamProxy(stream['NAME'], self, self.datastream_update_rate_hz))

    def send_parlay_command(self, command, **kwargs):
        """
        Manually send a parlay command. Returns a handle that can be paused on
        """
        #send the message and block for response
        msg = self._script.make_msg(self.item_id, self._command_id_lookup[command], msg_type=MSG_TYPES.COMMAND,
                                direct=True, response_req=True, COMMAND=command, **kwargs)
        self._script.send_parlay_message(msg, timeout=self.timeout, wait=False)
        return CommandHandle(msg, self._script)




    # Some re-implementation so our instance-bound descriptors will work instead of having to be class-bound.
    # Thanks: http://blog.brianbeck.com/post/74086029/instance-descriptors
    def __getattribute__(self, name):
        value = object.__getattribute__(self, name)
        if isinstance(value, ParlayStandardScriptProxy.PropertyProxy) or \
                isinstance(value, ParlayStandardScriptProxy.StreamProxy):
            value = value.__get__(self, self.__class__)
        return value

    def __setattr__(self, name, value):
        try:
            obj = object.__getattribute__(self, name)
        except AttributeError:
            pass
        else:
            if isinstance(obj, ParlayStandardScriptProxy.PropertyProxy) or \
                    isinstance(obj, ParlayStandardScriptProxy.StreamProxy):
                return obj.__set__(self, value)
        return object.__setattr__(self, name, value)

# register the proxy so it can be used in scripts
ITEM_PROXIES['ParlayStandardItem'] = ParlayStandardScriptProxy
ITEM_PROXIES['ParlayCommandItem'] =  ParlayStandardScriptProxy

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
        #:type ParlayScript
        self._script = script
        self.msg_list = []  # list of al messages with the same message id but swapped TO and FROM
        self._done = False  # True when we're done listening (So we can clean up)

        self._complete_deferred = defer.Deferred()
        self._ack_deferred = defer.Deferred()

        # add our listener
        self._script.add_listener(self._generic_on_message)


    def _generic_on_message(self, msg):
        """
        Listener function thatpowers the handle.
        This should only be called by a script in the reactor thread in its listener loop
        """

        topics, contents = msg["TOPICS"], msg["CONTENTS"]
        if topics.get("MSG_ID", None) == self._msg_topics["MSG_ID"] \
                and topics.get("TO", None) == self._msg_topics["FROM"] \
                and topics.get("FROM", None) == self._msg_topics['TO']:
            # add it to the list if the msg ids match but to and from are swapped
            self.msg_list.append(msg)

            #so if this status is OK or an error. if it is, then we're done listening
            status = topics.get("MSG_STATUS", None)
            type = topics.get("MSG_TYPE", None)
            if type == MSG_TYPES.RESPONSE and status != MSG_STATUS.ACK:
                #  if it's a response but not an ack, then we're done
                self._done = True
                if status == MSG_STATUS.ERROR:  # errback on error
                    self._complete_deferred.errback(failure.Failure(BadStatusError(msg)))
                else:
                    self._complete_deferred.callback(msg)
            elif type == MSG_TYPES.RESPONSE and status == MSG_STATUS.ACK:
                # new deferred because we could get more than one ACK
                old_ack, self._ack_deferred = self._ack_deferred, defer.Deferred()
                old_ack.callback(msg)


        # if we're done then  eat us (true true)
        return self._done


    def wait_for_complete(self):
        """
        Called from a scripts thread. Blocks until the message is complete.
        """
        return threads.blockingCallFromThread(self._script.reactor, lambda: self._complete_deferred)

    def wait_for_ack(self):
        """
        Called from a scripts thread. Blocks until the message is ackd
        """
        return threads.blockingCallFromThread(self._script.reactor, lambda: self._ack_deferred)










