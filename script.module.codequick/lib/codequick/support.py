# -*- coding: utf-8 -*-
from __future__ import absolute_import

# Standard Library Imports
import binascii
import logging
import inspect
import time
import json
import sys
import re

# Kodi imports
import xbmcaddon
import xbmcgui
import xbmc

# Package imports
from codequick.utils import parse_qs, ensure_native_str, ensure_bytes, urlparse

# Level mapper to convert logging module levels to kodi logger levels
log_level_map = {10: xbmc.LOGDEBUG,    # logger.debug
                 20: xbmc.LOGNOTICE,   # logger.info
                 30: xbmc.LOGWARNING,  # logger.warning
                 40: xbmc.LOGERROR,    # logger.error
                 50: xbmc.LOGFATAL}    # logger.critical

script_data = xbmcaddon.Addon("script.module.codequick")
addon_data = xbmcaddon.Addon()

plugin_id = addon_data.getAddonInfo("id")
logger_id = re.sub("[ .]", "-", addon_data.getAddonInfo("name"))

# Logger specific to this module
logger = logging.getLogger("%s.support" % logger_id)

# Listitem auto sort methods
auto_sort = set()


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
        self.debug_msgs = []

    def emit(self, record):
        """
        Forward the log record to kodi, lets kodi handle the logging.

        :param logging.LogRecord record: The log event record.
        """
        formatted_msg = ensure_native_str(self.format(record))
        log_level = record.levelno

        # Forward the log record to kodi with translated log level
        xbmc.log(formatted_msg, log_level_map[log_level])

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


class CacheProperty(object):
    """
    Converts a class method into a property. When property is accessed for the first time the result is computed
    and returned. The class property is then replaced with an instance attribute with the computed result.
    """

    def __init__(self, func):
        self.__name__ = func.__name__
        self.__doc__ = func.__doc__
        self._func = func

    def __get__(self, instance, owner):
        if instance:
            attr = self._func(instance)
            setattr(instance, self.__name__, attr)
            return attr
        else:
            return self


class Params(dict):
    """
    Parse the query string giving by kodi into a dictionary.
    Also splits the params into callback/support params.

    :param str raw_params: The query string passeed in from kodi.
    :ivar dict callback_params: Parameters that will be forward to callback function.
    :ivar dict support_params: Parameters used for internal functions.
    """

    def __init__(self, raw_params):
        super(Params, self).__init__()
        self.callback_params = {}
        self.support_params = {}

        if raw_params:
            if raw_params.startswith("_json_="):
                # Decode params using binascii & json
                raw_params = json.loads(binascii.unhexlify(raw_params[7:]))
            else:
                # Decode params using urlparse.parse_qs
                raw_params = parse_qs(raw_params)

            # Populate dict of params
            super(Params, self).__init__(raw_params)

            # Construct separate dictionaries for callback and support params
            for key, value in self.items():
                if key.startswith(u"_") and key.endswith(u"_"):
                    self.support_params[key] = value
                else:
                    self.callback_params[key] = value


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

    # noinspection PyDeprecation
    def args_to_kwargs(self, args):
        """
        Convert positional arguments to keyword arguments.

        :param tuple args: List of positional arguments to extract names for.
        :returns: A list of tuples consisten of ('arg name', 'arg value)'.
        :rtype: list
        """
        try:
            # noinspection PyUnresolvedReferences
            callback_args = inspect.getfullargspec(self.callback).args[1:]
        except AttributeError:
            # "inspect.getargspec" is deprecated in python 3
            callback_args = inspect.getargspec(self.callback).args[1:]

        return zip(callback_args, args)

    def unittest_caller(self, *args, **kwargs):
        """
        Function to allow callbacks to be easily called from unittests.
        Parent argument will be auto instantiated and passed to callback.
        This basically acts as a constructor to callback.

        :param args: Positional arguments to pass to callback.
        :param kwargs: Keyword arguments to pass to callback.
        :returns: The response from the callback function.
        """
        # Change the selector to match callback route been tested
        # This will ensure that the plugin paths are currect
        dispatcher.selector = self.path

        # Update support params with the params
        # that are to be passed to callback
        if args:
            arg_map = self.args_to_kwargs(args)
            dispatcher.params.update(arg_map)
        if kwargs:
            dispatcher.params.update(kwargs)

        # Instantiate the parent
        controller_ins = self.parent()

        try:
            # Now we are ready to call the callback function and return its results
            return self.callback(controller_ins, *args, **kwargs)
        finally:
            # Reset global datasets
            kodi_logger.debug_msgs = []
            dispatcher.reset()
            auto_sort.clear()


class Dispatcher(object):
    """Class to handle registering and dispatching of callback functions."""

    def __init__(self):
        # Extract command line arguments passed in from kodi
        self.selector, self.handle, self.params = self.parse_sysargs()
        self.selector_org = self.selector
        self.registered_routes = {}

        # List of callback functions that will be executed
        # after listitems have been listed
        self.metacalls = []

    def reset(self):
        """Reset session parameters."""
        self.selector = self.selector_org
        self.metacalls[:] = []
        self.params.clear()

    @staticmethod
    def parse_sysargs():
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
            raise RuntimeError("No parameters found, unable to execute script")

        # Extract command line arguments and remove leading '/' from selector
        _, _, route, raw_params, _ = urlparse.urlsplit(sys.argv[0] + sys.argv[2])
        route = route.split("/", 1)[-1]
        return route if route else "root", int(sys.argv[1]), Params(raw_params)

    def __getitem__(self, route):
        """:rtype: Route"""
        return self.registered_routes[route]

    def __missing__(self, route):
        raise KeyError("missing required route: '{}'".format(route))

    @property
    def callback(self):
        """
        The original callback function/class.

        Primarily used by 'Listitem.next_page' constructor.
        :returns: The dispatched callback function/class.
        """
        return self[self.selector].org_callback

    def register(self, callback, cls):
        """
        Register route callback function

        :param callback: The callback function.
        :param cls: Parent class that will handle the callback, if registering a function.
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
                # Set the callback as the parent and the run method as the function to call
                route = Route(callback, callback.run, callback, path)
                # noinspection PyTypeChecker
                callback.test = staticmethod(route.unittest_caller)
            else:
                raise NameError("missing required 'run' method for class: '{}'".format(callback.__name__))
        else:
            # Register a function callback
            route = Route(cls, callback, callback, path)
            callback.test = route.unittest_caller

        # Return original function undecorated
        self.registered_routes[path] = route
        callback.route = route
        return callback

    def dispatch(self):
        """Dispatch to selected route path."""
        try:
            # Fetch the controling class and callback function/method
            route = self[self.selector]
            logger.debug("Dispatching to route: '%s'", self.selector)
            logger.debug("Callback parameters: '%s'", self.params.callback_params)
            execute_time = time.time()

            # Initialize controller and execute callback
            controller_ins = route.parent()
            # noinspection PyProtectedMember
            controller_ins._execute_route(route.callback)
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


def build_path(path=None, query=None, **extra_query):
    """
    Build addon url that can be passeed to kodi for kodi to use when calling listitems.

    :param path: [opt] The route selector path referencing the callback object. (default => current route selector)
    :param query: [opt] A set of query key/value pairs to add to plugin path.
    :param extra_query: [opt] Keyword arguments if given will be added to the current set of querys.

    :return: Plugin url for kodi.
    :rtype: str
    """

    # If extra querys are given then append the
    # extra querys to the current set of querys
    if extra_query:
        query = dispatcher.params.copy()
        query.update(extra_query)

    # Encode the query parameters using json
    if query:
        query = "_json_=" + ensure_native_str(binascii.hexlify(ensure_bytes(json.dumps(query))))

    # Build kodi url with new path and query parameters
    return urlparse.urlunsplit(("plugin", plugin_id, path if path else dispatcher.selector, query, ""))


# Setup kodi logging
kodi_logger = KodiLogHandler()
base_logger = logging.getLogger()
base_logger.addHandler(kodi_logger)
base_logger.setLevel(logging.DEBUG)
base_logger.propagate = False

# Dispatcher to manage route callbacks
dispatcher = Dispatcher()