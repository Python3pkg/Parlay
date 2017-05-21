"""
We've added a number of enhancments to the Twisted reactor to enable easier threading handling.
Instead of importing the twisted.internet reactor import this reactor like
from parlay.server.reactor import reactor
"""
from twisted.internet import reactor as twisted_reactor, defer
import _thread as python_thread
from twisted.internet import threads as twisted_threads
import functools


class ReactorWrapper(object):
    def __init__(self, wrapped_reactor):
        self._reactor = wrapped_reactor
        self._thread = python_thread.get_ident()
        self._thread = None

    def run(self, installSignalHandlers=True):
        self._thread = python_thread.get_ident()
        return self._reactor.run(installSignalHandlers=installSignalHandlers)

    def __getattr__(self, item):
        return getattr(self._reactor, item)

    def in_reactor_thread(self):
        """
        Returns true if we're in the reactor thread context. False otherwise
        """
        current_thread = python_thread.get_ident()
        return current_thread == self._thread

    def maybeblockingCallFromThread(self, callable, *args, **kwargs):
        """
        Call callable from the reactor thread.  If we are in the reactor thread, then call it and return a Deferred.
        If we are *not* in the reactor thread, then block on that deferred instal of returning it
        """
        # if we're in the reactor thread then just call it
        if self.in_reactor_thread():
            return callable(*args, **kwargs)
        else:
            return twisted_threads.blockingCallFromThread(self._reactor, lambda: callable(*args, **kwargs))

    def maybeCallFromThread(self, callable, *args, **kwargs):
        """
        If we're in a separate thread from the reactor, then call from the reactor thread.
        If we're in the reactor thread, then schedule for call later in 0 seconds
        """

        if self.in_reactor_thread():
            self._reactor.callLater(0, lambda: callable(*args, **kwargs))
        else:
            self._reactor.callFromThread(lambda: callable(*args, **kwargs))

    def maybeDeferToThread(self, callable, *args, **kwargs):
        """
        Call callable from DIFFERENT THREAD.
        If we're already in a different thread, then JUST CALL IT and return result
        If we're in the reactor thead, then call it in a different thread and return a deferred with the result
        """
        if self.in_reactor_thread():             # wrap in lambda so we don't collide on kwargs with defertoThread
            return twisted_threads.deferToThread(lambda: callable(*args, **kwargs))
        else:
            return callable(*args, **kwargs)


def run_in_reactor(reactor):
        """
        Decorator for automatically handling deferred <-> thread handoff. Any function wrapped in this will work in both
        a threaded context and a 'reactor' async context. Use this decorator if you want the function to always be run
        in the reactor context

        :param reactor  the Reactor to use (MUST BE A REACTORWRAPPER)

        """
        def decorator(fn):
            # run this in reactor context
            return functools.wraps(fn)(lambda *args, **kwargs: reactor.maybeblockingCallFromThread(fn, *args, **kwargs))

        return decorator


def run_in_thread(reactor):
        """
        Decorator for automatically handling deferred <-> thread handoff. Any function wrapped in this will work in both
        a threaded context and a 'reactor' async context. Use this decorator if you want the function to always be run
        in the threaded context
        :param reactor  the Reactor to use (MUST BE A REACTORWRAPPER)

        """
        def decorator(fn):
            # run this command synchronously in a separate thread
            return functools.wraps(fn)(lambda *args, **kwargs: reactor.maybeDeferToThread(fn, *args, **kwargs))

        return decorator


reactor = ReactorWrapper(twisted_reactor)
