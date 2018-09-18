# Kodi gettext support
This module is used to load in all the string ID references from the "strings.po" file into codequick.
Allowing you to use a string as the localized string reference in the codequick framework.

This feature is not available directly within codequick as Kodi add-on's are not allowed to
directly parse the strings.po file. But the "gettext_kodi" module is designed so you can
directly package it with your own add-on. It just won't be allowed in the official Kodi repo.

# Usage
At the top of your add-on's python file where all your imports are, you just need to import gettext_kodi.
Then just call gettext_kodi.load_strings() to load all the string references into codequick.
