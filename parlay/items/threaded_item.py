from twisted.internet import defer
from parlay.items.base import MSG_TYPES, MSG_STATUS
from parlay.protocols.utils import message_id_generator
from twisted.python.failure import Failure
from .base import BaseItem
from parlay.server.broker import Broker, run_in_broker
import sys
import json
import logging


# a list of Item proxy classes for Scripts
ITEM_PROXIES = {}


DEFAULT_TIMEOUT = 120


# list of deferreds to cancel when cleaning up
CLEANUP_DEFERRED = set()


def cleanup():
    for d in CLEANUP_DEFERRED:
        if not d.called:
            d.cancel()


# cleanup our deferreds
Broker.call_on_stop(cleanup)


class ListenerStatus(object):
    """
    Enum object for keeping or removing listeners
    """
    KEEP_LISTENER = False
    REMOVE_LISTENER = True


class ThreadedItem(BaseItem):
    """Base object for all Parlay scripts"""
    # a list of functions that will be alerted when a new script instance is created
    stop_reactor_on_close = True

    def __init__(self, item_id, name, reactor=None, adapter=None):
        BaseItem.__init__(self, item_id, name, adapter=adapter)
        self._reactor = self._adapter.reactor if reactor is None else reactor
        self._msg_listeners = []
        self._system_errors = []
        self._system_events = []
        self._timer = None

        self._auto_update_discovery = True  #: If True auto update discovery with broadcast discovery messages
        self.discovery = {}  #: The current discovery information to pull from

        self._message_id_generator = message_id_generator(65535, 100)

        # Add this listener so it will be first in the list to pickup errors, warnings and events.
        self.add_listener(self._system_listener)
        self.add_listener(self._discovery_request_listener)

        self._adapter.subscribe(self._discovery_broadcast_listener, type='DISCOVERY_BROADCAST')


    # we need to overrite publish so we can register our callback for broker type messages
    def publish(self, msg):
        self._adapter.publish(msg, self._runListeners)

    def _discovery_broadcast_listener(self, msg):
        """
        Listen for discovery broadcast listeners and update our discovery accordingly
        """
        if self._auto_update_discovery and msg['CONTENTS'].get("status", "") == "ok":
            self.discovery = msg['CONTENTS'].get('discovery', self.discovery)

        return ListenerStatus.KEEP_LISTENER

    def _system_listener(self, msg):
        """
        This should be the first listener in the list. It will store any non-response errors and events
        :param msg:Msg from the broker
        :return: False so that it is never removed
        """

        # If it is not a response and it is an error, save it in the list
        if msg['TOPICS'].get('MSG_TYPE', "") != 'RESPONSE':
            status = msg['TOPICS'].get('MSG_STATUS', "")
            if status == 'ERROR':
                self._system_errors.append(msg)
            elif status == 'WARNING' or status == 'INFO':
                self._system_events.append(msg)
        return ListenerStatus.KEEP_LISTENER

    def _discovery_request_listener(self, msg):
        """
        Respond to a get_protocol_discovery message with an empty get_protocol_discovery_response message.
        :param msg: incoming message
        :return: False so that it is never removed from the listener list
        """
        if msg['TOPICS'].get('type', "") == 'get_protocol_discovery':
            msg = {'TOPICS': {'type': 'get_protocol_discovery_response'},
                   'CONTENTS': {"CHILDREN": [self.get_discovery()]}}
            self.publish(msg)
        return ListenerStatus.KEEP_LISTENER

    def open(self, protocol, **params):
        """
        :param protocol: protocol being used
        :param params: other parameters
        :return:
        """
        msg = {"TOPICS": {'type': 'broker', 'request': 'open_protocol'},
               "CONTENTS": {'protocol_name': protocol, 'params': params}}
        self._reactor.maybeCallFromThread(self.publish, msg)

        def wait_for_response():
            result = defer.Deferred()

            def listener(_msg):
                if _msg['TOPICS'].get('response', "") == 'open_protocol_response':
                    if _msg['CONTENTS']['STATUS'] == 'ok':
                        result.callback(_msg['CONTENTS']['STATUS'])
                    else:
                        result.errback(Failure(RuntimeError(_msg['CONTENTS']['STATUS'])))
                    return True  # we're done here
                return False  # keep waiting

            self.add_listener(listener)
            return result

        return self._reactor.maybeblockingCallFromThread(wait_for_response)

    def close_protocol(self, protocol_id):
        """
        close a protocol by id
        :param protocol_id:
        :return:
        """
        msg = {"TOPICS": {'type': 'broker', 'request': 'close_protocol'},
               "CONTENTS": {'protocol': protocol_id}}
        self._reactor.maybeCallFromThread(self.publish, msg)

        def wait_for_response():
            result = defer.Deferred()

            def listener(_msg):
                if _msg['TOPICS'].get('response', "") == 'close_protocol_response':
                    if _msg['CONTENTS']['STATUS'] == 'ok':
                        result.callback(_msg['CONTENTS']['STATUS'])
                    else:
                        result.errback(Failure(RuntimeError(_msg['CONTENTS']['STATUS'])))
                    return True  # we're done here
                return False  # keep waiting

            self.add_listener(listener)
            return result

        return self._reactor.maybeblockingCallFromThread(wait_for_response)

    def add_listener(self, listener_function):
        """
        Add  functions to the listener list
        """
        self._msg_listeners.append(listener_function)

    ###############################################################################################
    ###################  The functions below are used by the script ###############################
    def make_msg(self, to, command, msg_type=MSG_TYPES.COMMAND, direct=True,
                 response_req=True, _extra_topics=None, **kwargs):
        """
        Prepare a message for the broker to disperse
        """
        msg = {'TOPICS': {}, 'CONTENTS': kwargs}
        if _extra_topics is not None:
            msg["TOPICS"] = _extra_topics

        # we can assume some keyword values
        msg['TOPICS']['TX_TYPE'] = 'DIRECT' if direct else "BROADCAST"
        msg['TOPICS']['MSG_TYPE'] = msg_type
        msg['TOPICS']['RESPONSE_REQ'] = response_req
        msg['TOPICS']['MSG_ID'] = next(self._message_id_generator)
        msg['TOPICS']['TO'] = to
        msg['TOPICS']['FROM'] = self.item_id
        if command is not None:
            msg['CONTENTS']['COMMAND'] = command
        return msg

    def send_parlay_message(self, msg, timeout=DEFAULT_TIMEOUT, wait=None):
        """
        Send a command.  This will be sent from the reactor thread.  If a response is required, we will wait
        for it.
        :param msg The Message to send
        :param timeout If we require a response and don't get one back int timeout seconds, raise a timeout exception
        :param wait If set to True, will block until a response, if false will continue without blocking,
        If set to None, till auto discover based on message RESPONSE_REQ.
        """
        if wait is None:
            wait = msg['TOPICS'].get('RESPONSE_REQ', False)

        if wait:
            # block the thread until we get a response or timeout
            return self._reactor.maybeblockingCallFromThread(self._send_parlay_message_from_thread,
                                                            msg=msg, timeout=timeout)
        else:
            # send this to the reactor without waiting for a response
            self._reactor.maybeCallFromThread(self.publish, msg)
            return None  # nothing to wait on, no response

    def discover(self, force=True):
        """
        Run a discovery so that the script knows what items are attached and can get handles to them.

        :param force: If True, will force a rediscovery, if False will take the last cached discovery
        """
        if not self._reactor.running:
            raise Exception("You must call parlay.utils.setup() at the beginning of a script!")

        print("Running discovery...")
        # block the thread until we get a discovery or error
        return self._reactor.maybeblockingCallFromThread(self._in_reactor_discover, force)

    def save_discovery(self, path):
        """
        Save the current discovery information to a file so it can be loaded later

        :param path: The Path to the file to save to (Warning: will be overwritten)
        """
        with open(path, "w") as f:
            # pretty print in case a human wants to read it
            json.dump(self.discovery, f, indent=4, sort_keys=True)

    def load_discovery(self, path):
        """
        Load discovery from a file.

        :param path: The path to the file that has the JSON discovery
        """
        with open(path, 'r') as f:
            self.discovery = json.load(f)

    @run_in_broker
    @defer.inlineCallbacks
    def get_item_by_id(self, item_id):
        """
        Returns a handler object that can be used to send messages to an item.

        :param item_id: globally unique id of the item
        :return: a proxy object for the item
        """

        if not self._reactor.running:
            raise Exception("You must call parlay.utils.setup() at the beginning of a script!")

        def find():
            g = self._find_item_info(self.discovery, item_id, "ID")
            item_disc = next(g)
            return self._proxy_item(item_disc)

        try:
            defer.returnValue(find())
        except StopIteration:
            # discover and try again
            try:
                yield self.discover(force=False)
                defer.returnValue(find())
            except StopIteration:
                raise KeyError("Couldn't find item with id " + str(item_id))

    @run_in_broker
    @defer.inlineCallbacks
    def get_item_by_name(self, item_name):
        """
        Returns a handler object that can be used to send messages to an item.

        :param item_name: globally unique name of the item
        :return: a proxy object for the item
        """

        if not self._reactor.running:
            raise Exception("You must call parlay.utils.setup() at the beginning of a script!")

        def find():
            g = self._find_item_info(self.discovery, item_name, "NAME")
            item_disc = next(g)
            return self._proxy_item(item_disc)

        try:
            defer.returnValue(find())
        except StopIteration:
            # discover and try again
            try:
                yield self.discover(force=False)
                defer.returnValue(find())
            except StopIteration:
                raise KeyError("Couldn't find item with name " + str(item_name))

    @run_in_broker
    @defer.inlineCallbacks
    def get_all_items_with_name(self, item_name):
        """
        Returns a handler object that can be used to send messages to an item.

        :param item_name: globally unique name of the item
        :return: a proxy object for the item
        """

        if not self._reactor.running:
            raise Exception("You must call parlay.utils.setup() at the beginning of a script!")

        result = [self._proxy_item(x) for x in self._find_item_info(self.discovery, item_name, "NAME")]
        if len(result) == 0:  # retry after discover if it fails
            yield self.discover(force=False)
            result = [self._proxy_item(x) for x in self._find_item_info(self.discovery, item_name, "NAME")]

        defer.returnValue(result)



    def _proxy_item(self, item_disc):
        """
        Get an Item Proxy object by the discovery

        :param item_disc: The item discovery object
        :type item_disc: dict
        """
        # Do we have a valid item discovery object?
        if item_disc is None:
            raise KeyError("Couldn't make Proxy Item with None type")

        # now that we have the discovery, let's try and construct a proxy out of it
        templates = [x.strip() for x in item_disc.get("TYPE", "").split("/")]
        template = None
        # find and stop at the first valid one
        for t in templates:
            if template is None:
                template = ITEM_PROXIES.get(t, None)

        # if it's still None, then that means that we couldn't find it
        if template is None:
            raise KeyError("Couldn't find template proxy for:" + item_disc.get("TYPE", ""))

        # we have a good template class! Let's construct it
        try:
            result = template(item_disc, self)
            return result
        except Exception as e:
            print("Could not construct proxy Item. Caught Exception :" + str(e))
            raise

    def _find_item_info(self, discovery, item_id, key):
        """
        Find the item with a given id recursively (or None if it can't be found)

        :type: discovery list
        """
        for item in discovery:
            if item.get(key, None) == item_id:
                yield item
            # see if a child found it
            for child in self._find_item_info(item.get('CHILDREN', []), item_id, key):
                yield child

    def sleep(self, timeout):
        """
        Sleep for <timeout> seconds.  This call is BLOCKING.

        :param timeout: number of seconds to sleep
        """

        if not self._reactor.running:
            raise Exception("You must call parlay.utils.setup() at the beginning of a script!")

        return run_in_broker(lambda: self._sleep(timeout))()

    ####################### The following  must be run from the reactor thread ###################
    #############################   Do not call directly from script thread #####################
    def _send_parlay_message_from_thread(self, msg, timeout):
        """
        Send the command and wait for the callback.This must be called
        only from the reactor thread.
        NOTE: caller is blocked.
        :param msg: message to send
        :param timeout timeout ins econds
        """
        response = defer.Deferred()
        timer = None
        timeout_msg = {'TOPICS': {'MSG_TYPE': 'TIMEOUT'}}

        def listener(received_msg):
            # See if this is the response we are waiting for
            if received_msg['TOPICS'].get('MSG_TYPE', "") == MSG_TYPES.RESPONSE:
                if received_msg['TOPICS']['TO'] == self.item_id and \
                        received_msg['TOPICS'].get('MSG_ID', None) == msg['TOPICS']['MSG_ID']:

                    if received_msg['TOPICS'].get('MSG_STATUS', "") == MSG_STATUS.PROGRESS:
                        return False  # keep waiting, an ACK means its not finished yet, it just got our msg
                    if timer is not None:
                        # Clear the timer
                        timer.cancel()
                    if received_msg['TOPICS'].get('MSG_STATUS', "") == MSG_STATUS.ERROR:
                        # return error to waiting thread
                        response.errback(Failure(ErrorResponse(received_msg)))
                    else:
                        # send the response back to the waiting thread
                        response.callback(received_msg)
                    return True  # remove this listener from the list

            else:  # not our response.  Check for a system error.
                if len(self._system_errors) > 0:
                    if timer is not None:
                        # clear out the timer
                        timer.cancel()
                    # report an error to the waiting thread
                    response.errback(Failure(AsyncSystemError(self._system_errors.pop(0))))
                    return True  # remove this listener

            return False  # not for this listener - don't remove

        def cb(_msg):
            # got a timeout or started with an error
            # remove the listener
            if listener in self._msg_listeners:
                self._msg_listeners.remove(listener)
            # send failure to thread waiting.
            response.errback(Failure(AsyncSystemError(_msg)))

        # If we already have a system error, fail
        if len(self._system_errors) > 0:
            self._timer = self._reactor.callLater(0, cb, self._system_errors.pop(0))

        else:
            # set a timeout, if requested
            if timeout > 0:
                timer = self._reactor.callLater(timeout, cb, timeout_msg)

            # add our listener to the listener ist
            self.add_listener(listener)

            # send the message
            self.publish(msg)

        return response



    def _in_reactor_discover(self, force):
        """
        Discovery called from within the reactor context
        """
        # call this back with the discovery
        result = defer.Deferred()

        def discovery_listener(msg):
            if msg['TOPICS'].get("type", "") != 'broker' and \
                    msg['TOPICS'].get("response", "") != "get_discovery_response":
                return False  # not the msg we're looking for

            if msg['CONTENTS'].get("status", "") == "ok":
                self.discovery = msg['CONTENTS'].get('discovery', {})
                result.callback(self.discovery)
            else:
                result.errback(Failure(Exception(msg.get("status", "NO STATUS"))))

            return True  # we're done here

        self.add_listener(discovery_listener)

        self.publish({"TOPICS": {'type': 'broker', 'request': 'get_discovery'},
                                   "CONTENTS": {'force': force}})

        return result

    def _sleep(self, timeout):
        """
        Support a script delay.  The delay will stop early with an error if there is a system error.
        :param timeout:
        :return:deferred
        """
        response = defer.Deferred()
        CLEANUP_DEFERRED.add(response)
        timer = None

        def listener(received_msg):
            # look for system errors while we are waiting
            if len(self._system_errors) > 0:
                # cancel out the timer
                if timer is not None:
                    timer.cancel()
                # return the error to our waiting thread
                response.errback(Failure(AsyncSystemError(self.errors.pop(0))))
                return True  # remove the listener from the list
            return False  # don't remove

        def cb(msg):
            # remove ourselves from cleanup list
            CLEANUP_DEFERRED.remove(response)
            # remove our listener function if it is in the list.
            if listener in self._msg_listeners:
                self._msg_listeners.remove(listener)

            # if this is the normal timeout, just send the timeout message
            if msg['TOPICS']['MSG_TYPE'] == 'TIMEOUT':
                # Timed out - no error
                response.callback(msg)
            else:
                # Error
                response.errback(Failure(AsyncSystemError(msg)))

        # check we don't already have an error
        if len(self._system_errors) > 0:
            self._timer = self._reactor.callLater(0, cb, self._system_errors.pop(0))
        else:
            timer = self._reactor.callLater(timeout, cb, {'TOPICS': {'MSG_TYPE': 'TIMEOUT'}})
            self.add_listener(listener)
        return response

    def _runListeners(self, msg):
        remove_list = []
        for i, listener in enumerate(self._msg_listeners):
            if listener(msg):
                remove_list.append(i)

        # Now that we are done running the list, we can remove the ones slated for removal.
        if len(remove_list) > 0:
            self._msg_listeners = [x for i, x in enumerate(self._msg_listeners) if i not in remove_list]


class ErrorResponse(Exception):
    def __init__(self, error_msg):
        self.error_msg = error_msg
        self.description = error_msg['CONTENTS'].get('DESCRIPTION', '')

        # copy our traceback from the other exception
        self._traceback = error_msg["CONTENTS"].get('TRACEBACK', None)
        self.str = "Response Error: {} \n\n\nOriginal Traceback:\n {}".format(self.description, self._traceback)

    def __str__(self):
        return self.str


class AsyncSystemError(Exception):
    """
    This error class is for asynchronous system errors.
    """
    def __init__(self, error_msg):
        self.error_msg = error_msg
        self.description = error_msg.get('CONTENTS', {}).get('DESCRIPTION', '')
        self.code = error_msg.get('CONTENTS', {}).get('ERROR_CODE', 0)

    def __str__(self):
        return "Critical Error: {} \nCODE: {} \nERROR_MESSAGE: {} ".format(self.description, self.code, self.error_msg)
