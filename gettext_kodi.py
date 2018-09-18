# -*- coding: utf-8 -*-
from codequick.support import addon_data
from codequick.utils import string_map
import xbmc
import re
import os


def load_strings():
    """
    The function loads in all the string id references from the "strings.po" file into codequick.
    Allowing you to use a string as the localized string reference in the codequick framework.
    """

    # Check if path needs to be translated first
    addon_path = addon_data.getAddonInfo("path")
    if addon_path[:10] == "special://":  # pragma: no cover
        addon_path = xbmc.translatePath(addon_path)

    # Location of strings.po file
    strings_po = os.path.join(addon_path, "resources", "language", "resource.language.en_gb", "strings.po")
    strings_po = strings_po.decode("utf8") if isinstance(strings_po, bytes) else strings_po

    # Check if strings.po actrally exists first
    if os.path.exists(strings_po):  # pragma: no branch
        with open(strings_po, "rb") as fo:
            raw_strings = fo.read()

        # Parse strings using Regular Expressions
        res = u"^msgctxt\s+[\"']#(\d+?)[\"']$[\n\r]^msgid\s+[\"'](.+?)[\"']$"
        data = re.findall(res, raw_strings.decode("utf8"), re.MULTILINE | re.UNICODE)
        string_map.update((key, int(value)) for value, key in data)
