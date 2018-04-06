# -*- coding: utf-8 -*-
from __future__ import absolute_import

# Standard Library Imports
import binascii
import logging
import inspect
import pickle
import time
import sys
import re

# Kodi imports
import xbmcaddon
import xbmcgui
import xbmc

# Package imports
from codequick.utils import parse_qs, ensure_native_str, urlparse

script_data = xbmcaddon.Addon("script.module.codequick")
addon_data = xbmcaddon.Addon()

plugin_id = addon_data.getAddonInfo("id")
logger_id = re.sub("[ .]", "-", addon_data.getAddonInfo("name"))

# Logger specific to this module
logger = logging.getLogger("%s.support" % logger_id)

# Listitem auto sort methods
auto_sort = set()


class LoggingMap(dict):
    def __init__(self):
        super(LoggingMap, self).__init__()
        self[10] = xbmc.LOGDEBUG    # logger.debug
        self[20] = xbmc.LOGNOTICE   # logger.info
        self[30] = xbmc.LOGWARNING  # logger.warning
        self[40] = xbmc.LOGERROR    # logger.error
        self[50] = xbmc.LOGFATAL    # logger.critical

    def __missing__(self, key):
        """Return log notice for any unexpected log level."""
        return xbmc.LOGNOTICE


class KodiLogHandler(logging.Handler):
    """
    Custom Logger Handler to forward logs to Kodi.

    Log records will automatically be converted from unicode to utf8 encoded strings.
    All debug messages will be stored locally and outputed as warning messages if a critical error occurred.
    This is done so that debug messages will appear on the normal kodi log file without having to enable debug logging.

    :ivar debug_msgs: Local store of degub messages.
    """
    def __init__(self):
        super(KodiLogHandler, self).__init__()
        self.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
        self.log_level_map = LoggingMap()
        self.debug_msgs = []

    def emit(self, record):
        """
        Forward the log record to kodi, lets kodi handle the logging.

        :param logging.LogRecord record: The log event record.
        """
        formatted_msg = ensure_native_str(self.format(record))
        log_level = record.levelno

        # Forward the log record to kodi with translated log level
        xbmc.log(formatted_msg, self.log_level_map[log_level])

        # Keep a history of all debug records so they can be logged later if a critical error occurred
        # Kodi by default, won't show debug messages unless debug logging is enabled
        if log_level == 10:
            self.debug_msgs.append(formatted_msg)

        # If a critical error occurred, log all debug messages as warnings
        elif log_level == 50 and self.debug_msgs:
            xbmc.log("###### debug ######", xbmc.LOGWARNING)
            for msg in self.debug_msgs:
                xbmc.log(msg, xbmc.LOGWARNING)
            xbmc.log("###### debug ######", xbmc.LOGWARNING)


class Route(object):
    """
    Handle callback route data.

    :param parent: The parent class that will handle the response from callback.
    :param callback: The callable callback function.
    :param org_callback: The decorated func/class.
    :param str path: The route path to func/class.

    :ivar is_playable: True if callback is playable, else False.
    :ivar is_folder: True if callback is a folder, else False.
    :ivar org_callback: The decorated func/class.
    :ivar callback: The callable callback function.
    :ivar parent: The parent class that will handle the response from callback.
    :ivar path: The route path to func/class.
    """
    __slots__ = ("parent", "callback", "org_callback", "path", "is_playable", "is_folder")

    def __init__(self, parent, callback, org_callback, path):
        self.is_playable = parent.is_playable
        self.is_folder = parent.is_folder
        self.org_callback = org_callback
        self.callback = callback
        self.parent = parent
        self.path = path

    def args_to_kwargs(self, args, kwargs):
        """
        Convert positional arguments to keyword arguments and merge into callback parameters.

        :param tuple args: List of positional arguments to extract names for.
        :param dict kwargs: The dict of callback parameters that will be updated.
        :returns: A list of tuples consisting of ('arg name', 'arg value)'.
        :rtype: list
        """
        callback_args = self.arg_names()[1:]
        arg_map = zip(callback_args, args)
        kwargs.update(arg_map)

    def arg_names(self):
        """Return a list of argument names, positional and keyword arguments."""
        try:
            # noinspection PyUnresolvedReferences
            return inspect.getfullargspec(self.callback).args
        except AttributeError:
            # "inspect.getargspec" is deprecated in python 3
            return inspect.getargspec(self.callback).args

    def unittest_caller(self, *args, **kwargs):
        """
        Function to allow callbacks to be easily called from unittests.
        Parent argument will be auto instantiated and passed to callback.
        This basically acts as a constructor to callback.

        Test specific Keyword args:
        execute_delayed: Execute any registered delayed callbacks.

        :param args: Positional arguments to pass to callback.
        :param kwargs: Keyword arguments to pass to callback.
        :returns: The response from the callback function.
        """
        execute_delayed = kwargs.pop("execute_delayed", False)

        # Change the selector to match callback route been tested
        # This will ensure that the plugin paths are currect
        dispatcher.selector = self.path

        # Update support params with the params
        # that are to be passed to callback
        if args:
            self.args_to_kwargs(args, dispatcher.params)

        if kwargs:
            dispatcher.params.update(kwargs)

        # Instantiate the parent
        controller_ins = self.parent()

        try:
            # Now we are ready to call the callback function and return its results
            results = self.callback(controller_ins, *args, **kwargs)
            if inspect.isgenerator(results):
                return list(results)
            else:
                return results
        finally:
            # Execute Delated callback functions if any
            if execute_delayed:
                dispatcher.run_metacalls()

            # Reset global datasets
            kodi_logger.debug_msgs = []
            dispatcher.reset()
            auto_sort.clear()


class Dispatcher(object):
    """Class to handle registering and dispatching of callback functions."""

    def __init__(self):
        # Extract command line arguments passed in from kodi
        self.selector = "root"
        self.handle = -1

        self.params = {}
        self.callback_params = {}
        self.registered_routes = {}

        # List of callback functions that will be executed
        # after listitems have been listed
        self.metacalls = []

    def reset(self):
        """Reset session parameters."""
        self.selector = "root"
        self.metacalls[:] = []
        self.params.clear()

    def parse_sysargs(self):
        """
        Extract route selector & callback params from the command line arguments received from kodi.

        Selector is the path to the route callback.
        Handle is the id used for kodi to handle requests send from this addon.
        Params are the dictionary of parameters that controls the execution of this framework.

        :return: A tuple of (selector, handle, params)
        :rtype: tuple
        """
        # Only designed to work as a plugin
        if not sys.argv[0].startswith("plugin://"):
            raise RuntimeError("Only plugin:// paths are supported: not {}".format(sys.argv[0]))

        # Extract command line arguments and remove leading '/' from selector
        _, _, route, raw_params, _ = urlparse.urlsplit(sys.argv[0] + sys.argv[2])
        route = route.split("/", 1)[-1]

        self.selector = route if route else "root"
        self.handle = int(sys.argv[1])
        if raw_params:
            self.parse_params(raw_params)

    def parse_params(self, raw_params):
        if raw_params.startswith("_pickle_="):
            # Decode params using binascii & json
            raw_params = pickle.loads(binascii.unhexlify(raw_params[9:]))
        else:
            # Decode params using urlparse.parse_qs
            raw_params = parse_qs(raw_params)

        # Populate dict of params
        self.params.update(raw_params)

        # Construct separate dictionaries for callback params
        self.callback_params = {key: value for key, value in self.params.items()
                                if not (key.startswith(u"_") and key.endswith(u"_"))}

    @property
    def current_route(self):
        """
        The original callback function/class.

        Primarily used by 'Listitem.next_page' constructor.
        :returns: The dispatched callback function/class.
        """
        return self[self.selector]

    def register(self, callback, cls):
        """
        Register route callback function

        :param callback: The callback function.
        :param cls: Parent class that will handle the callback, used when callback is a function.
        :returns: The callback function with extra attributes added, 'route', 'testcall'.
        """
        if callback.__name__.lower() == "root":
            path = callback.__name__.lower()
        else:
            path = "{}/{}".format(callback.__module__.strip("_").replace(".", "/"), callback.__name__).lower()

        if path in self.registered_routes:
            raise ValueError("encountered duplicate route: '{}'".format(path))

        # Register a class callback
        elif inspect.isclass(callback):
            if hasattr(callback, "run"):
                # Set the callback as the parent and the run method as the 'run' function
                route = Route(callback, callback.run, callback, path)
                tester = staticmethod(route.unittest_caller)
            else:
                raise NameError("missing required 'run' method for class: '{}'".format(callback.__name__))
        else:
            # Register a function callback
            route = Route(cls, callback, callback, path)
            tester = route.unittest_caller

        # Return original function undecorated
        self.registered_routes[path] = route
        callback.route = route
        callback.test = tester
        return callback

    def register_delayed(self, func, args, kwargs):
        callback = (func, args, kwargs)
        self.metacalls.append(callback)

    def dispatch(self):
        """Dispatch to selected route path."""
        self.parse_sysargs()

        try:
            # Fetch the controling class and callback function/method
            route = self[self.selector]
            logger.debug("Dispatching to route: '%s'", self.selector)
            logger.debug("Callback parameters: '%s'", self.callback_params)
            execute_time = time.time()

            # Initialize controller and execute callback
            parent_ins = route.parent()
            results = route.callback(parent_ins, **self.callback_params)
            if hasattr(parent_ins, "_process_results"):
                # noinspection PyProtectedMember
                parent_ins._process_results(results)

        except Exception as e:
            # Log the error in both the gui and the kodi log file
            dialog = xbmcgui.Dialog()
            dialog.notification(e.__class__.__name__, str(e), addon_data.getAddonInfo("icon"))
            logger.critical(str(e), exc_info=1)

        else:
            from . import start_time
            logger.debug("Route Execution Time: %ims", (time.time() - execute_time) * 1000)
            logger.debug("Total Execution Time: %ims", (time.time() - start_time) * 1000)
            self.run_metacalls()

    def run_metacalls(self):
        """Execute all callbacks, if any."""
        if self.metacalls:
            # Time before executing callbacks
            start_time = time.time()

            # Execute each callback one by one
            for func, args, kwargs in self.metacalls:
                try:
                    func(*args, **kwargs)
                except Exception as e:
                    logger.exception(str(e))

            # Log execution time of callbacks
            logger.debug("Callbacks Execution Time: %ims", (time.time() - start_time) * 1000)

    def __getitem__(self, route):
        """:rtype: Route"""
        return self.registered_routes[route]


def build_path(callback=None, args=None, query=None, **extra_query):
    """
    Build addon url that can be passeed to kodi for kodi to use when calling listitems.

    :param callback: [opt] The route selector path referencing the callback object. (default => current route selector)
    :param tuple args: [opt] Positional arguments that will be add to plugin path.
    :param dict query: [opt] A set of query key/value pairs to add to plugin path.
    :param extra_query: [opt] Keyword arguments if given will be added to the current set of querys.

    :return: Plugin url for kodi.
    :rtype: str
    """

    # Set callback to current callback if not given
    route = callback.route if callback else dispatcher.current_route

    # Convert args to keyword args if required
    if args:
        route.args_to_kwargs(args, query)

    # If extra querys are given then append the
    # extra querys to the current set of querys
    if extra_query:
        query = dispatcher.params.copy()
        query.update(extra_query)

    # Encode the query parameters using json
    if query:
        query = "_pickle_=" + ensure_native_str(binascii.hexlify(pickle.dumps(query, protocol=pickle.HIGHEST_PROTOCOL)))

    # Build kodi url with new path and query parameters
    return urlparse.urlunsplit(("plugin", plugin_id, route.path, query, ""))


# Setup kodi logging
kodi_logger = KodiLogHandler()
base_logger = logging.getLogger()
base_logger.addHandler(kodi_logger)
base_logger.setLevel(logging.DEBUG)
base_logger.propagate = False

# Dispatcher to manage route callbacks
dispatcher = Dispatcher()
run = dispatcher.dispatch
