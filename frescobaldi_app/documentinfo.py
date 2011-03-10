# This file is part of the Frescobaldi project, http://www.frescobaldi.org/
#
# Copyright (c) 2008, 2009, 2010 by Wilbert Berendsen
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
# See http://www.gnu.org/licenses/ for more information.

from __future__ import unicode_literals

"""
Delivers information about a document.
"""

import itertools
import functools
import os
import re
import weakref

from PyQt4.QtCore import QSettings, QUrl

import ly.tokenize.lilypond
import app
import tokeniter
import util
import plugin
import variables


def info(document):
    """Returns a DocumentInfo instance for the given Document."""
    return DocumentInfo.instance(document)


def mode(document, guess=True):
    """Returns the type of the given document. See DocumentInfo.mode()."""
    return info(document).mode(guess)


def textmode(text, guess=True):
    """Returns the type of the given text ('lilypond, 'html', etc.).
    
    Checks the mode variable and guesses otherwise if guess is True.
    
    """
    mode = variables.variables(text).get("mode")
    if mode in ly.tokenize.modes:
        return mode
    if guess:
        return ly.tokenize.guessMode(text)


def includeargs(tokens):
    """Yields the arguments of \\include commands in the token stream."""
    for token in tokens:
        if isinstance(token, ly.tokenize.lilypond.Keyword) and token == "\\include":
            for token in tokens:
                if not isinstance(token, (ly.tokenize.Space, ly.tokenize.Comment)):
                    break
            if token == '"':
                yield ''.join(itertools.takewhile(lambda t: t != '"', tokens))


def includeargsinfile(filename):
    """Returns the list of arguments of \\include commands in the given file.
    
    The return value is cached until the mtime of the file changes.
    
    """
    global _include_args_cache
    try:
        cache = _include_args_cache
    except NameError:
        import filecache
        cache = _include_args_cache = filecache.FileCache()
    try:
        return cache[filename]
    except KeyError:
        with open(filename) as f:
            text = util.decode(f.read())
        result = cache[filename] = list(includeargs(ly.tokenize.state(textmode(text)).tokens(text)))
        return result
        

def outputargs(tokens):
    """Yields the arguments of \\bookOutputName, \\bookOutputSuffix and define output-suffix commands.
    
    Every argument is a two tuple(type, argument) where type is either "suffix" or "name".
    
    """
    for token in tokens:
        found = None
        if isinstance(token, ly.tokenize.lilypond.Command):
            if token == "\\bookOutputName":
                found = "name"
            elif token == "\\bookOutputSuffix":
                found = "suffix"
        elif isinstance(token, ly.tokenize.scheme.Word) and token == "output-suffix":
            found = "suffix"
        if found:
            for token in tokens:
                if not isinstance(token, (ly.tokenize.lilypond.SchemeStart,
                                          ly.tokenize.Space,
                                          ly.tokenize.Comment)):
                    break
            if token == '"':
                yield found, ''.join(itertools.takewhile(lambda t: t != '"', tokens))


def outputargsinfile(filename):
    """Returns the list of arguments of \\bookOutputName, \\bookOutputSuffix etc. commands.
    
    See outputargs(). The return value is cached until the mtime of the file changes.
    
    """
    global _output_args_cache
    try:
        cache = _output_args_cache
    except NameError:
        import filecache
        cache = _output_args_cache = filecache.FileCache()
    try:
        return cache[filename]
    except KeyError:
        with open(filename) as f:
            text = util.decode(f.read())
        result = cache[filename] = list(outputargs(ly.tokenize.state(textmode(text)).tokens(text)))
        return result


def resetoncontentschanged(func):
    """Caches a value until the document emits the contentsChanged signal.
    
    Use this to decorate methods of the DocumentInfo class.
    
    """
    _cache = weakref.WeakKeyDictionary()
    @functools.wraps(func)
    def f(self):
        try:
            return _cache[self]
        except KeyError:
            def reset(selfref=weakref.ref(self)):
                self = selfref()
                if self:
                    del _cache[self]
                    self.document().contentsChanged.disconnect(reset)
            result = _cache[self] = func(self)
            self.document().contentsChanged.connect(reset)
            return result
    return f


class DocumentInfo(plugin.DocumentPlugin):
    """Computes and caches (sometimes) information about a Document."""
    
    def __init__(self, document):
        pass
    
    def mode(self, guess=True):
        """Returns the type of document ('lilypond, 'html', etc.).
        
        The mode can be set using the "mode" document variable.
        If guess is True (default), the mode is auto-recognized based on the contents
        if not set explicitly using the "mode" variable. In this case, this function
        always returns an existing mode.
        
        If guess is False, auto-recognizing is not done and the function returns None
        if the mode wasn't set explicitly.
        
        """
        mode = variables.get(self.document(), "mode")
        if mode in ly.tokenize.modes:
            return mode
        if guess:
            return ly.tokenize.guessMode(self.document().toPlainText())
    
    def tokens(self):
        """Iterates over all the tokens in a document, parsing if the document has not yet materialized."""
        if self.document().materialized():
            return tokeniter.allTokens(self.document())
        else:
            return ly.tokenize.state(self.mode()).tokens(self.document().toPlainText())

    @resetoncontentschanged
    def version(self):
        """Returns the LilyPond version if set in the document, as a tuple of ints.
        
        First the functions searches inside LilyPond syntax.
        Then it looks at the 'version' document variable.
        Then, if the document is not a LilyPond document, it simply searches for a
        \\version command string, possibly embedded in a comment.
        
        The version is cached until the documents contents change.
        
        """
        source = self.tokens()
        for token in source:
            if isinstance(token, ly.tokenize.lilypond.Keyword) and token == "\\version":
                for token in source:
                    if not isinstance(token, (ly.tokenize.Space, ly.tokenize.Comment)):
                        break
                if token == '"':
                    pred = lambda t: t != '"'
                else:
                    pred = lambda t: not isinstance(t, ly.tokenize.Space, ly.tokenize.Comment)
                version = ''.join(itertools.takewhile(pred, source))
                return tuple(map(int, re.findall(r"\d+", version)))
        # look at document variables
        version = variables.get(self.document(), "version")
        if version:
            return tuple(map(int, re.findall(r"\d+", version)))
        # parse whole document for non-lilypond documents
        if self.mode() != "lilypond":
            m = re.search(r'\\version\s*"(\d+\.\d+(\.\d+)*)"', self.document().toPlainText())
            if m:
                return tuple(map(int, m.group(1).split('.')))
    
    def master(self):
        """Returns the master filename for the document, if it exists."""
        filename = self.document().url().toLocalFile()
        redir = variables.get(self.document(), "master")
        if filename and redir:
            path = os.path.normpath(os.path.join(os.path.dirname(filename), redir))
            if os.path.exists(path) and path != filename:
                return path

    def includepath(self):
        """Returns the configured include path. Currently the document does not matter."""
        return QSettings().value("lilypond_settings/include_path", []) or []
        
    def jobinfo(self, create=False):
        """Returns a three tuple(filename, mode, includepath) based on the given document.
        
        If the document is a local file, its contents is checked for the 'master' variable
        to run the engraver on a different file instead. The mode is then also chosen
        based on the contents of that other file.
        
        If no redirecting variables are found and the document is modified, its text
        is saved to a temporary area and that filename is returned. If the 'create'
        argument is False (the default), no temporary file is created, and in that
        case, the existing filename (may be empty) is returned.
        
        If a scratch area is used but the document has a local filename and includes
        other files, the original directory is given in the includepath list.
        
        """
        # Determine the filename to run the engraving job on
        includepath = []
        filename = self.master()
        if filename:
            with open(filename) as f:
                text = util.decode(f.read())
            mode_ = textmode(text)
        else:
            filename = self.document().url().toLocalFile()
            mode_ = self.mode()
        
            if not filename or self.document().isModified():
                # We need to use a scratchdir to save our contents to
                import scratchdir
                scratch = scratchdir.scratchdir(self.document())
                if create:
                    scratch.saveDocument()
                    if filename and self.hasinclude():
                        includepath.append(os.path.dirname(filename))
                    filename = scratch.path()
                elif scratch.path() and os.path.exists(scratch.path()):
                    filename = scratch.path()
        
        return filename, mode_, includepath
    
    @resetoncontentschanged
    def hasinclude(self):
        """Returns True if the document contains an \\include command."""
        for token in self.tokens():
            if isinstance(token, ly.tokenize.lilypond.Keyword) and token == "\\include":
                return True
        
    @resetoncontentschanged
    def includeargs(self):
        """Returns a list of \\include arguments in our document."""
        return list(includeargs(self.tokens()))

    def includefiles(self):
        """Returns a set of filenames that are included by the given document.
        
        The document's own filename is also added to the set.
        The configured include path is used to find files.
        
        """
        files = set()
        ipath = self.includepath()
        
        def tryarg(directory, arg):
            path = os.path.join(directory, arg)
            if os.path.exists(path) and path not in files:
                files.add(path)
                args = includeargsinfile(path)
                find(args, os.path.dirname(path))
                return True
                
        def find(incl_args, directory):
            for arg in incl_args:
                # new, recursive, relative include
                if not (directory and tryarg(directory, arg)):
                    # old include (relative to master file)
                    if not (basedir and tryarg(basedir, arg)):
                        # if path is given, also search there:
                        for p in ipath:
                            if tryarg(p, arg):
                                break
                    
        filename = self.master()
        if filename:
            incl_args = includeargsinfile(filename)
        else:
            filename = self.document().url().toLocalFile()
            if filename:
                incl_args = self.includeargs()
        if filename:
            files.add(filename)
            basedir = os.path.dirname(filename)
            find(incl_args, basedir)
        return files

    @resetoncontentschanged
    def outputargs(self):
        """Returns a list of output arguments (see outputargs() in this module) in our document."""
        return list(outputargs(self.tokens()))
        
    def basenames(self):
        """Returns a set of basenames that a document is expected to create.
        
        The list is created based on include files and the define output-suffix and
        \bookOutputName and \bookOutputSuffix commands.
        You should add '.ext' and/or '-[0-9]+.ext' to find created files.
        
        """
        basenames = set()
        filename, mode = self.jobinfo()[:2]
        basepath = os.path.splitext(filename)[0]
        dirname, basename = os.path.split(basepath)
        
        if mode == "lilypond":
            includes = self.includefiles()
            if basepath:
                basenames.add(basepath)
                
            def args():
                if not self.master():
                    includes.discard(self.document().url().toLocalFile())
                    yield self.outputargs()
                for filename in includes:
                    yield outputargsinfile(filename)
                        
            for type, arg in itertools.chain.from_iterable(args()):
                if type == "suffix":
                    arg = basename + '-' + arg
                basenames.add(os.path.normpath(os.path.join(dirname, arg)))
        
        elif mode == "html":
            pass
        
        elif mode == "texinfo":
            pass
        
        elif mode == "latex":
            pass
        
        elif mode == "docbook":
            pass
        
        return basenames


