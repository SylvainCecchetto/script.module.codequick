# -*- coding: utf-8 -*-

# Standard Library Imports
from functools import partial
import logging
import re

# Kodi imports
import xbmcplugin
import xbmcgui
import xbmc

# Package imports
from .support import Dispatcher, Script, build_path, handle, logger_id

# Logger specific to this module
logger = logging.getLogger("%s.api" % logger_id)

# Dispatcher to manage route callbacks
dispatcher = Dispatcher()

# Localized string Constants
SELECT_PLAYBACK_ITEM = 25006


class Route(Script):
    """Add Directory List Items to Kodi"""

    # Change listitem type to 'folder'
    is_folder = True

    def __init__(self):
        super(Route, self).__init__()
        self._manual_sort = set()
        self._autosort = True

        self.update_listing = self.params.get("_updatelisting_", False)
        """bool: True, this folder should update the current listing. False, this folder is a subfolder(Default)."""

    def execute_route(self, callback):
        """Execute the callback function and process the results."""

        # Fetch all listitems from callback function
        listitems = super(Route, self).execute_route(callback)

        # Process listitems and close
        self.__add_listitems(listitems)
        self.__add_sort_methods(self._manual_sort)
        self.__end_directory(True)

    def __add_listitems(self, raw_listitems):
        if not raw_listitems:
            raise RuntimeError("No listitems found")

        # Create a new list containing tuples, consisting of path, listitem, isfolder.
        listitems = []
        folder_counter = 0
        for listitem in raw_listitems:
            if listitem:
                listitem_tuple = listitem.close()
                listitems.append(listitem_tuple)
                if listitem_tuple[2]:
                    folder_counter += 1

        # Pass the listitems and relevant data to kodi
        xbmcplugin.addDirectoryItems(handle, listitems, len(listitems))

        # Guess if this directory listing is primarily a folder or a video listing.
        # Listings will be considered to be a folder if more that half the listitems are folder items.
        isfolder = folder_counter > (len(listitems) / 2)
        self.__content_type(isfolder)

    def __content_type(self, isfolder):
        """Guess content type and set kodi parameters, setContent & SetViewMode"""

        # Set the add-on content type
        xbmcplugin.setContent(handle, "albums")

        # Sets the category for skins to display modes.
        xbmcplugin.setPluginCategory(handle, re.sub("\(\d+\)$", "", self._title).strip())

        # Change preferred view mode if one was set for given content type
        set_key = "{}.{}.view".format(xbmc.getSkinDir(), "folder" if isfolder else "video")
        view_mode = self.setting[set_key]
        if view_mode:
            xbmc.executebuiltin("Container.SetViewMode({})".format(view_mode))

    def __add_sort_methods(self, manual):
        """Add sort methods to kodi"""
        if self._autosort:
            manual.update(auto_sort)

        if manual:
            # Sort the list of sort methods before adding to kodi
            _addSortMethod = xbmcplugin.addSortMethod
            for sortMethod in sorted(manual):
                _addSortMethod(handle, sortMethod)
        else:
            # If no sortmethods are given then set sort mehtod to unsorted
            xbmcplugin.addSortMethod(handle, xbmcplugin.SORT_METHOD_UNSORTED)

    def __end_directory(self, success):
        """Mark the end of directory listings."""
        xbmcplugin.endOfDirectory(handle, success, self.update_listing, False)

    def add_sort_methods(self, *methods, **override):
        """
        Adds sorting method(s) for the media list.

        Any number of sort mehtods can be given as multiple arguments.
        Normally this should not be needed as sort methods are auto detected.

        :param int methods: One or more kodi sort mehtods.
        :param bool override: (Optional) keyword argument to override the auto selected sort methods. (Default: False)
        """
        # Disable auto sort if override is True, Default
        self._autosort = self._autosort and not override.get("override", False)
        self._manual_sort.update(methods)

    @staticmethod
    def set_category(category):
        """
        Sets the plugins name for skins to display.

        :param category: Plugins sub category.
        :type category: str, unicode
        
        # Need to test if this function can be more usefull
        """
        xbmcplugin.setPluginCategory(handle, category)


class Resolver(Script):
    # Change listitem type to 'player'
    is_playable = True

    def execute_route(self, callback):
        """Execute the callback function and process the results."""
        resolved = super(Resolver, self).execute_route(callback)
        self.__send_to_kodi(resolved)

    def create_loopback(self, url, **next_params):
        """
        Create a playlist where the second item loops back to current addon to load next video. e.g. Party Mode

        :param url: Url for the first playable listitem.
        :type url: str or unicode

        :param next_params: (Optional) Keyword arguments to add to the loopback request when accessing the next video.

        :returns: The Listitem that kodi will play
        :rtype: :class:`xbmcgui.ListItem`
        """
        # Create Playlist
        playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)

        # Create Main listitem
        main_listitem = xbmcgui.ListItem()
        main_listitem.setPath(url)

        if self._title.startswith(u"_loopback_"):
            main_listitem.setLabel(self._title.split(u" - ", 1)[1])
            next_params["title"] = self._title
        else:
            # Add playable listitem as the first playlist item
            main_listitem.setLabel(self._title)
            next_params["title"] = u"_loopback_ - %s" % self._title
            playlist.clear()
            playlist.add(url, main_listitem)

        # Create Loopback listitem
        loop_listitem = xbmcgui.ListItem()
        loop_listitem.setLabel(next_params["title"])

        # Build a loopback url that callback to the addon to fetch the next video
        loopback_url = build_path(**next_params)
        loop_listitem.setPath(loopback_url)
        playlist.add(loopback_url, loop_listitem)

        # Retrun the playable listitem
        return main_listitem

    def extract_source(self, url, quality=None):
        """
        Extract video url using YoutubeDL

        Options for quality parameter:
            0=SD
            1=720p
            2=1080p
            3=4K

        :param url: Url to fetch video for.
        :type url: str or unicode
        :param int quality: (Optional) Override youtubeDL's quality setting.

        :returns: The extracted video url
        :rtype: str
        """
        try:
            # noinspection PyUnresolvedReferences
            from YDStreamExtractor import getVideoInfo, disableDASHVideo
        except ImportError:
            self.log("YoutubeDL module not installed. Please install to enable 'extract_source'", lvl=40)
            raise ImportError("Missing YoutubeDL module")
        else:
            # Disable DASH videos, until youtubeDL supports it
            disableDASHVideo(True)

            # If there is more than one stream found then ask for selection
            video_info = getVideoInfo(url, quality)
            if video_info.hasMultipleStreams():
                # Ask the user to select a stream
                return self.__source_selection(video_info)
            else:
                return video_info.streamURL()

    def __source_selection(self, video_info):
        """
        Ask user with video stream to play

        :param video_info: YDStreamExtractor video_info object
        :returns: Stream url of video
        :rtype: str
        """
        display_list = []
        for stream in video_info.streams():
            data = "%s - %s" % (stream["ytdl_format"]["extractor"].title(), stream["title"])
            display_list.append(data)

        dialog = xbmcgui.Dialog()
        ret = dialog.select(self.localize(SELECT_PLAYBACK_ITEM), display_list)
        if ret >= 0:
            video_info.selectStream(ret)
            return video_info.streamURL()

    def __create_playlist(self, urls):
        """
        Create playlist for kodi and return back the first item of that playlist to play.

        :param list urls: Set of urls that will be used in the creation of the playlist.

        :returns The first listitem of the playlist.
        :rtype: :class:`xbmcgui.ListItem`:
        """
        # Create Playlist
        playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        title = self._title.encode("utf8")

        # Loop each item to create playlist
        for count, url in enumerate(urls, 1):
            listitem = xbmcgui.ListItem()
            if isinstance(url, (list, tuple)):
                url, title = url
                if isinstance(title, unicode):
                    title = title.encode("utf8")

            # Create listitem with new title
            listitem.setLabel("%s Part %i" % (title, count))
            listitem.setPath(url)

            # Populate Playlis
            playlist.add(url, listitem)

        # Return first playlist item to send to kodi
        return playlist[0]

    def __send_to_kodi(self, resolved):
        """ Construct playable listitem and send to kodi

        :param resolved: The resolved url to send back to kodi
        :type resolved: str or unicode or :class:`xbmcgui.ListItem`
        """

        # Create listitem object if resolved object is a string or unicode
        if isinstance(resolved, (str, unicode)):
            listitem = xbmcgui.ListItem()
            listitem.setPath(resolved)

        # Create playlist if resolved a list of urls
        elif isinstance(resolved, (list, tuple)):
            listitem = self.__create_playlist(resolved)

        # Use resoleved as is if its already a listitem
        elif isinstance(resolved, xbmcgui.ListItem):
            listitem = resolved

        # Invalid or No url was found
        elif resolved:
            raise ValueError("resolver returned invalid url: '%s'" % type(resolved))
        else:
            raise ValueError("resolver failed to return url")

        # Send playable listitem to kodi
        xbmcplugin.setResolvedUrl(handle, True, listitem)


def custom_route(path, parent=None):
    """
    Decorator used to register callback functions/classes with custom route path.

    :param str path: The route path for registering callback to.
    :param parent: (Optional) The parent control class, if decorating a function.
    :returns: A preconfigured register decorator.
    """
    return partial(dispatcher.register, cls=parent, custom_route=path)


register_script = partial(dispatcher.register, cls=Script)
register_script.__doc__ = """Decorator used to register 'Script' callback function/class."""

register_route = partial(dispatcher.register, cls=Route)
register_route.__doc__ = """Decorator used to register 'VirtualFS' callback function/class."""

register_resolver = partial(dispatcher.register, cls=Resolver)
register_resolver.__doc__ = """Decorator used to register 'PlayMedia' callback function/class."""

run = dispatcher.dispatch

from .listing import auto_sort
