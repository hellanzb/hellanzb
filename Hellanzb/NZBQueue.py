"""

NZBQueue - Maintains NZB files queued to be downloaded in the future

(c) Copyright 2005 Philip Jenvey
[See end of file]
"""
import os, re, time, Hellanzb, Hellanzb.Daemon
from shutil import copy, move, rmtree
from twisted.internet import reactor
from Hellanzb.Log import *
from Hellanzb.Util import archiveName, hellaRename, getFileExtension, toUnicode, validNZB

__id__ = '$Id$'

class NZBQueue(object):
    """ NZBQueue maintains pending NZB files to be downloaded. Only the NZB  """
    def __init__(self, nzbQueueDir = None):
        if nzbQueueDir is None:
            nzbQueueDir = Hellanzb.QUEUE_DIR
        self.nzbQueueDir = nzbQueueDir

        self.queuedNZBs = []

        self.append, self.remove = self.queuedNZBs.append, self.queuedNZBs.remove

    def __iter__(self):
        return iter(self.queuedNZBs)

def scanQueueDir(firstRun = False, justScan = False):
    """ Find new/resume old NZB download sessions """
    t = time.time()

    from Hellanzb.NZBLeecher.NZBModel import NZB
    current_nzbs = []
    for file in os.listdir(Hellanzb.CURRENT_DIR):
        if re.search(r'(?i)\.(nzb|xml)$', file):
            current_nzbs.append(Hellanzb.CURRENT_DIR + os.sep + file)

    # See if we're resuming a nzb fetch
    resuming = False
    displayNotification = False
    new_nzbs = []
    queuedMap = {}
    for nzb in Hellanzb.queued_nzbs:
        queuedMap[os.path.normpath(nzb.nzbFileName)] = nzb

    for file in os.listdir(Hellanzb.QUEUE_DIR):
        if re.search(r'(?i)\.(nzb|xml)$', file) and \
            os.path.normpath(Hellanzb.QUEUE_DIR + os.sep + file) not in queuedMap:
            new_nzbs.append(Hellanzb.QUEUE_DIR + os.sep + file)
            
        elif os.path.normpath(Hellanzb.QUEUE_DIR + os.sep + file) in queuedMap:
            queuedMap.pop(os.path.normpath(Hellanzb.QUEUE_DIR + os.sep + file))

    # Remove anything no longer in the queue directory
    for nzb in queuedMap.itervalues():
        Hellanzb.queued_nzbs.remove(nzb)

    enqueueNZBs(new_nzbs, writeQueue = not firstRun)
            
    if firstRun:
        sortQueueFromDisk()

    e = time.time() - t
    if justScan:
        # Done scanning -- don't bother loading a new NZB
        debug('Ziplick scanQueueDir (justScan): ' + Hellanzb.QUEUE_DIR + ' TOOK: ' + str(e))
        Hellanzb.downloadScannerID = reactor.callLater(7, scanQueueDir, False, True)
        return
    else:
        debug('Ziplick scanQueueDir: ' + Hellanzb.QUEUE_DIR)

    if not current_nzbs:
        if not Hellanzb.queued_nzbs or Hellanzb.downloadPaused:
            # Nothing to do, lets wait 5 seconds and start over
            reactor.callLater(5, scanQueueDir)
            return

        # Start the next download
        nzb = Hellanzb.queued_nzbs[0]
        nzbfilename = os.path.basename(nzb.nzbFileName)
        del Hellanzb.queued_nzbs[0]
    
        # nzbfile will always be a absolute filename 
        nzbfile = Hellanzb.QUEUE_DIR + nzbfilename
        move(nzbfile, Hellanzb.CURRENT_DIR)

        if not (len(new_nzbs) == 1 and len(Hellanzb.queued_nzbs) == 0):
            # Show what's going to be downloaded next, unless the queue was empty, and we
            # only found one nzb (The 'Found new nzb' message is enough in that case)
            displayNotification = True
    else:
        # Resume the NZB in the CURRENT_DIR
        nzbfilename = current_nzbs[0]
        nzb = NZB(nzbfilename)
        nzbfilename = os.path.basename(nzb.nzbFileName)
        displayNotification = True
        del current_nzbs[0]
        resuming = True

    nzbfile = Hellanzb.CURRENT_DIR + nzbfilename
    nzb.nzbFileName = nzbfile

    if resuming:
        parseNZB(nzb, 'Resuming')
    elif displayNotification:
        parseNZB(nzb)
    else:
        parseNZB(nzb, quiet = True)

def sortQueueFromDisk():
    """ sort the queue from what's on disk """
    onDiskQueue = loadQueueFromDisk()
    unsorted = Hellanzb.queued_nzbs[:]
    Hellanzb.queued_nzbs = []
    arranged = []
    for line in onDiskQueue:
        for nzb in unsorted:
            if os.path.basename(nzb.nzbFileName) == line:
                Hellanzb.queued_nzbs.append(nzb)
                arranged.append(nzb)
                break
    for nzb in arranged:
        unsorted.remove(nzb)
    for nzb in unsorted:
        Hellanzb.queued_nzbs.append(nzb)
            
def loadQueueFromDisk():
    """ load the queue from disk """
    queue = []
    if os.path.isfile(Hellanzb.QUEUE_LIST):
        try:
            f = open(Hellanzb.QUEUE_LIST)
        except:
            f.close()
            return queue
        for line in f:
            queue.append(line.strip('\n'))
        f.close()
    return queue

def writeQueueToDisk(queue):
    """ write the queue to disk """
    unique = []
    for item in queue:
        if item not in unique:
            unique.append(item)
    if len(unique) != len(queue):
        warn('Warning: Found duplicates in queue while writing to disk: ' + \
             str([nzb.nzbFileName for nzb in queue]))
    queue = unique
        
    f = open(Hellanzb.QUEUE_LIST, 'w')
    for nzb in queue:
        f.write(os.path.basename(nzb.nzbFileName) + '\n')
    f.close()

        
def parseNZB(nzb, notification = 'Downloading', quiet = False):
    """ Parse the NZB file into the Queue. Unless the NZB file is deemed already fully
    processed at the end of parseNZB, tell the factory to start downloading it """
    writeQueueToDisk(Hellanzb.queued_nzbs)

    if not quiet:
        info(notification + ': ' + nzb.archiveName)
        growlNotify('Queue', 'hellanzb ' + notification + ':', nzb.archiveName,
                    False)

    try:
        findAndLoadPostponedDir(nzb)
        
        info('Parsing: ' + os.path.basename(nzb.nzbFileName) + '...')
        if not Hellanzb.queue.parseNZB(nzb):
            Hellanzb.Daemon.beginDownload()

    except FatalError, fe:
        error('Problem while parsing the NZB', fe)
        growlNotify('Error', 'hellanzb', 'Problem while parsing the NZB' + prettyException(fe),
                    True)
        error('Moving bad NZB out of queue into TEMP_DIR: ' + Hellanzb.TEMP_DIR)
        move(nzb.nzbFileName, Hellanzb.TEMP_DIR + os.sep)
        reactor.callLater(5, scanQueueDir)

def ensureSafePostponedLoad(nzbFileName):
    """ Force doesn't immediately abort the download of the forced out NZB -- it lets the
     NZBLeechers currently working on them finish. We need to be careful of forced NZBs
     that are so small, that they finish downloading before these 'slower' NZBLeechers are
     even done with the previous, forced out NZB. The parseNZB function could end up
     colliding with the leechers, while pareseNZB looks for segments on disk/to be skipped
     """
    # Look for any NZBLeechers downloading files for the specified unpostponed NZB. They
    # are most likely left over from a force call, using a very small NZB.
    shouldCancel = False
    cancelledClients = []
    for nsf in Hellanzb.nsfs:
        for nzbl in nsf.clients:
            if nzbl.currentSegment != None and os.path.basename(nzbl.currentSegment.nzbFile.nzb.nzbFileName) == \
                    os.path.basename(nzbFileName):
                # the easiest way to prevent weird things from happening (such as the
                # parser getting confused about what needs to be downloaded/skipped) is to
                # just pull the trigger on those slow NZBLeechers connections --
                # disconnect them and ensure the segments they were trying to download
                # aren't requeued
                debug('Aborting/Disconnecting %s to ensure safe postponed NZB load' % str(nzbl))
                shouldCancel = True
                nzbl.currentSegment.dontRequeue = True
                cancelledClients.append(nzbl)

                # Can't recall the details of why we should manually loseConnection(), do
                # isLoggedIn and also deactivate() below -- but this is was
                # cancelCurrent() does
                nzbl.transport.loseConnection()
                nzbl.isLoggedIn = False

    if shouldCancel:
        # Also reset the state of the queue if we had to do any cleanup
        Hellanzb.queue.cancel()

        for nzbl in cancelledClients:
            nzbl.deactivate()
        
def findAndLoadPostponedDir(nzb):
    """ move a postponed working directory for the specified nzb, if one is found, to the
    WORKING_DIR """
    def fixNZBFileName(nzb):
        if os.path.normpath(os.path.dirname(nzb.destDir)) == os.path.normpath(Hellanzb.POSTPONED_DIR):
            nzb.destDir = Hellanzb.WORKING_DIR
        
    nzbfilename = nzb.nzbFileName
    d = Hellanzb.POSTPONED_DIR + os.sep + archiveName(nzbfilename)
    if os.path.isdir(d):
        try:
            os.rmdir(Hellanzb.WORKING_DIR)
        except OSError:
            files = os.listdir(Hellanzb.WORKING_DIR)[0]
            if len(files):
                name = files[0]
                ext = getFileExtension(name)
                if ext != None:
                    name = name.replace(ext, '')
                move(Hellanzb.WORKING_DIR, Hellanzb.TEMP_DIR + os.sep + name)

            else:
                debug('ERROR Stray WORKING_DIR!: ' + str(os.listdir(Hellanzb.WORKING_DIR)))
                name = Hellanzb.TEMP_DIR + os.sep + 'stray_WORKING_DIR'
                hellaRename(name)
                move(Hellanzb.WORKING_DIR, name)

        move(d, Hellanzb.WORKING_DIR)

        # unpostpone from the queue
        Hellanzb.queue.nzbFilesLock.acquire()
        arName = archiveName(nzbfilename)
        found = []
        for nzbFile in Hellanzb.queue.postponedNzbFiles:
            if nzbFile.nzb.archiveName == arName:
                found.append(nzbFile)
        for nzbFile in found:
            Hellanzb.queue.postponedNzbFiles.remove(nzbFile)
        Hellanzb.queue.nzbFilesLock.release()

        ensureSafePostponedLoad(nzb.nzbFileName)
        
        info('Loaded postponed directory: ' + archiveName(nzbfilename))

        fixNZBFileName(nzb)
        return True
    else:
        fixNZBFileName(nzb)
        return False

def moveUp(nzbId, shift = 1, moveDown = False):
    """ move the specified nzb up in the queue """
    try:
        nzbId = int(nzbId)
    except:
        debug('Invalid ID: ' + str(nzbId))
        return False
    try:
        shift = int(shift)
    except:
        debug('Invalid shift: ' + str(shift))
        return False
            
    i = 0
    foundNzb = None
    for nzb in Hellanzb.queued_nzbs:
        if nzb.id == nzbId:
            foundNzb = nzb
            break
        i += 1
        
    if not foundNzb:
        return False

    if i - shift <= -1 and not moveDown:
        # can't go any higher
        return False
    elif i + shift >= len(Hellanzb.queued_nzbs) and moveDown:
        # can't go any lower
        return False

    Hellanzb.queued_nzbs.remove(foundNzb)
    if not moveDown:
        Hellanzb.queued_nzbs.insert(i - shift, foundNzb)
    else:
        Hellanzb.queued_nzbs.insert(i + shift, foundNzb)
    writeQueueToDisk(Hellanzb.queued_nzbs)
    return True

def moveDown(nzbId, shift = 1):
    """ move the specified nzb down in the queue """
    return moveUp(nzbId, shift, moveDown = True)

def dequeueNZBs(nzbIdOrIds, quiet = False):
    """ remove nzbs from the queue """
    if type(nzbIdOrIds) != list:
        newNzbIds = [ nzbIdOrIds ]
    else:
        newNzbIds = nzbIdOrIds

    if len(newNzbIds) == 0:
        return False

    error = False
    found = []
    for nzbId in newNzbIds:
        try:
            nzbId = int(nzbId)
        except Exception:
            error = True
            continue
        
        for nzb in Hellanzb.queued_nzbs:
            if nzb.id == nzbId:
                found.append(nzb)
    for nzb in found:
        if not quiet:
            info('Dequeueing: ' + nzb.archiveName)
        move(nzb.nzbFileName, Hellanzb.TEMP_DIR + os.sep + os.path.basename(nzb.nzbFileName))
        Hellanzb.queued_nzbs.remove(nzb)
        
    writeQueueToDisk(Hellanzb.queued_nzbs)
    return not error

def enqueueNZBStr(nzbFilename, nzbStr):
    """ Write the specified NZB file (in string format) to disk and enqueue it """
    tempLocation = Hellanzb.TEMP_DIR + os.sep + nzbFilename
    if os.path.exists(tempLocation):
        if not os.access(tempLocation, os.W_OK):
            error('Unable to write NZB to temp location: ' + tempLocation)
            return
        
        rmtree(tempLocation)

    f = open(tempLocation, 'w')
    f.writelines(nzbStr)
    f.close()

    enqueueNZBs(tempLocation)
    os.remove(tempLocation)
    
def enqueueNZBs(nzbFileOrFiles, next = False, writeQueue = True):
    """ add one or a list of nzb files to the end of the queue """
    if type(nzbFileOrFiles) != list:
        newNzbFiles = [ nzbFileOrFiles ]
    else:
        newNzbFiles = nzbFileOrFiles

    if len(newNzbFiles) == 0:
        return False
    
    for nzbFile in newNzbFiles:
        if validNZB(nzbFile):
            if os.path.normpath(os.path.dirname(nzbFile)) != os.path.normpath(Hellanzb.QUEUE_DIR):
                copy(nzbFile, Hellanzb.QUEUE_DIR + os.sep + os.path.basename(nzbFile))
            nzbFile = Hellanzb.QUEUE_DIR + os.sep + os.path.basename(nzbFile)

            found = False
            for n in Hellanzb.queued_nzbs:
                if os.path.normpath(n.nzbFileName) == os.path.normpath(nzbFile):
                    found = True
                    error('Unable to add nzb file to queue: ' + os.path.basename(nzbFile) + \
                          ' it already exists!')
            if found:
                continue
                    
            from Hellanzb.NZBLeecher.NZBModel import NZB
            nzb = NZB(nzbFile)
            
            if not next:
                Hellanzb.queued_nzbs.append(nzb)
            else:
                Hellanzb.queued_nzbs.insert(0, nzb)

            msg = 'Found new nzb: '
            info(msg + archiveName(nzbFile))
            growlNotify('Queue', 'hellanzb ' + msg, archiveName(nzbFile), False)
                
    if writeQueue:
        writeQueueToDisk(Hellanzb.queued_nzbs)
            
def enqueueNextNZBs(nzbFileOrFiles):
    """ enqueue one or more nzbs to the beginning of the queue """
    return enqueueNZBs(nzbFileOrFiles, next = True)

def nextNZBId(nzbId):
    """ enqueue the specified nzb to the beginning of the queue """
    try:
        nzbId = int(nzbId)
    except:
        debug('Invalid ID: ' + str(nzbId))
        return False

    foundNZB = None
    for nzb in Hellanzb.queued_nzbs:
        if nzb.id == nzbId:
            foundNZB = nzb
            
    if not foundNZB:
        return True

    Hellanzb.queued_nzbs.remove(foundNZB)
    Hellanzb.queued_nzbs.insert(0, foundNZB)

    writeQueueToDisk(Hellanzb.queued_nzbs)
    return True

def lastNZB(nzbId):
    try:
        nzbId = int(nzbId)
    except:
        debug('Invalid ID: ' + str(nzbId))
        return False

    foundNZB = None
    for nzb in Hellanzb.queued_nzbs:
        if nzb.id == nzbId:
            foundNZB = nzb
            
    if not foundNZB:
        return True
    
    Hellanzb.queued_nzbs.remove(foundNZB)
    Hellanzb.queued_nzbs.append(foundNZB)

    writeQueueToDisk(Hellanzb.queued_nzbs)
    return True

def moveNZB(nzbId, index):
    try:
        nzbId = int(nzbId)
    except:
        debug('Invalid ID: ' + str(nzbId))
        return False
    try:
        index = int(index)
    except:
        debug('Invalid INDEX: ' + str(index))
        return False

    foundNZB = None
    for nzb in Hellanzb.queued_nzbs:
        if nzb.id == nzbId:
            foundNZB = nzb
            
    if not foundNZB:
        return True
    
    Hellanzb.queued_nzbs.remove(foundNZB)
    Hellanzb.queued_nzbs.insert(index - 1, foundNZB)

    writeQueueToDisk(Hellanzb.queued_nzbs)
    return True

def listQueue(includeIds = True, convertToUnicode = True):
    """ Return a listing of the current queue. By default this function will convert all
    strings to unicode, as it's only used right now for the return of XMLRPC calls """
    members = []
    for nzb in Hellanzb.queued_nzbs:
        if includeIds:
            name = archiveName(os.path.basename(nzb.nzbFileName))
            rarPassword = nzb.rarPassword
            
            if convertToUnicode:
                name = toUnicode(name)
                rarPassword = toUnicode(rarPassword)
                
            member = {'id': nzb.id,
                      'nzbName': name}
            
            if rarPassword != None:
                member['rarPassword'] = rarPassword
        else:
            member = os.path.basename(nzb.nzbFileName)
        members.append(member)
    return members
    
"""
/*
 * Copyright (c) 2005 Philip Jenvey <pjenvey@groovie.org>
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. The name of the author or contributors may not be used to endorse or
 *    promote products derived from this software without specific prior
 *    written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
 * OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 * HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 * LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
 * OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 * SUCH DAMAGE.
 *
 * $Id$
 */
"""