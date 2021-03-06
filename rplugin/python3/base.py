import pynvim
import re

log_file = open('log.txt','w')
def DPrintf(stringable):
    log_file.write(str(stringable))
    log_file.write('\n')
    log_file.flush()

def debug(f):
    def wrapper(*args):
        result = f(*args)
        DPrintf("Function Name = {} Output = {}".format(f.__name__,result))
        return result
    return wrapper
################ **** ##################
# TODO: all vim interactions should be done via this object
class WindowBufferPair(object):
    def __init__(self,window,buffer,vim):
        self.window = window
        self.buffer = buffer

        # for calling vim
        self.vim = vim
    def _getCurrCursorForced(self):
        return self.vim.request("nvim_win_get_cursor",self.window)
    def setCursor(self,match):
        """
        Note: Row + Column is now 1 indexed b/c we changed to excuting a direct vim command
        """
        if not match:
            return
        r = match[0]+1
        c = match[1][0]+1
        # self.vim.request("nvim_win_set_cursor",self.window,(r,c))
        # execute "normal " . target_line . "G" . target_col . "|"
        self.vim.request("nvim_exec","normal{}G{}| ".format(r,c),False)
    def getCurrLine(self):
        line_num,_ = self._getCurrCursorForced()
        result = self.vim.request("nvim_buf_get_lines",self.buffer,line_num-1,line_num,True)
        # DPrintf("Result: {}".format(result))
        # DPrintf("Vim Current Line: {}".format(self.vim.current.line))
        # assert result[0] == self.vim.current.line
        return result[0]
    def getLineRange(self):
        # cursor,_ = wb_pair._getCurrCursorForced() # line number is absolute
        abs_top = self._getLineFromWindowMotion("H")
        abs_bottom = self._getLineFromWindowMotion("L") # number already accounts for resize due to FilterJump

        page_content = self.vim.call("getbufline",self.buffer,abs_top,abs_bottom)
        return page_content,VimTranslator(abs_top)
    def _getLineFromWindowMotion(self, motion):
        # check jumplist doesn't get added to
        curr_cursor = self._getCurrCursorForced()

        # switch windows and make the move
        self.vim.call("win_gotoid",self.window) # now that we are back, nothing happens
        self.vim.command("keepjumps normal! " + motion) # w/o keep jumps, JumpList will add curr location to jumplist
        new_row,_ = self._getCurrCursorForced()

        # move back
        self.vim.call("cursor",curr_cursor[0],curr_cursor[1]) # Note: does not add to jumplist
        ## testing code
        # x,y = wb_pair._getCurrCursorForced()
        # if x != curr_cursor[0] or y != curr_cursor[1]:
            # DPrintf(curr_cursor)
            # DPrintf("\n")
            # DPrintf(str(x) + ","+str(y))
            # raise AssertionError
        return new_row
    def drawHighlights(self,highlighter):
        """
        Note: match_range should be exclusive at end
        Note: 0 indexed horizontally and vertically, despite vim frontend being otherwise
        """
        self.clearHighlights(highlighter)
        # EC: highlights + match are None
        if not highlighter.getCurrentMatch():
            return

        # 1. Highlight current selection
        first_line,first_match = highlighter.getCurrentMatch()
        self.vim.request("nvim_buf_add_highlight",self.buffer,highlighter.ns,"SearchCurrent",first_line,first_match[0],first_match[1])
        # 2. Highlight rest
        for (l,match_range) in highlighter.list_of_highlights:
            if l != first_line or match_range !=first_match:
                self.vim.request("nvim_buf_add_highlight",self.buffer,highlighter.ns,"SearchHighlights",l,match_range[0],match_range[1])
    def clearHighlights(self,highlighter):
        self.vim.request("nvim_buf_clear_namespace",self.buffer,highlighter.ns,0,-1)
    def destroyWindowBuffer(self):
        self.vim.request("nvim_buf_delete",self.buffer,{})



################ **** ##################
class VimTranslator(object):
    def __init__(self,abs_top):
        """
        Adjusting for frontend being 1 indexed while add_highlight being 0 indexed
        """
        self.abs_top = abs_top - 1
        self.x_offset = 0
    def _translate_y(self,rel_line):
        return self.abs_top + rel_line
    def _translate_x(self,range):
        return (range[0]+self.x_offset,range[1]+self.x_offset)
    # @debug
    def translateMatches(self,rel_line,list_of_ranges):
        return [(self._translate_y(rel_line),self._translate_x(range)) for range in list_of_ranges]


################ **** ##################
class CompressedString(object):
    def __init__(self,string,set_of_strip_characters=['_']):
        new_string = []
        index_map = []
        for i,char in enumerate(string.lower()):
            if char not in set_of_strip_characters:
                new_string.append(char)
                index_map.append(i)
        new_string = ''.join(new_string)
        self.c_string = new_string
        self.index_map = index_map
        self.length = len(new_string)
    def getString(self):
        return self.c_string
    def _expand(self,start,end):
        if end == self.length:
            return self.index_map[start],self.index_map[self.length-1] +1
        else:
            return self.index_map[start], self.index_map[end]
    # @debug
    def expandMatches(self,matches):
        return [self._expand(match.start(),match.end()) for match in matches]
    @staticmethod
    def createArrayOfCompressedStrings(page_content,set_of_strip_characters):
        """
        Note: compressed strings will also be lowercased
        """
        compressed_range = []
        for string in page_content:
            compressed_range.append(CompressedString(string,set_of_strip_characters))
        return compressed_range

################ **** ##################
class Highlighter(object):
    def __init__(self,ns):
        self.ns = ns

        self.list_of_highlights = []
        # TODO: combine current_match + idx into just an iterator/refactor incrementIndex
        self.current_match = None
        self.idx = 0
        self.variable_to_print = None
    def update_highlighter(self,list_of_highlights):
        # EC: no highlight matches
        if not list_of_highlights:
            self.variable_to_print = None
            self.list_of_highlights = []
            return

        # Case 1: No Previous Matches: Just make first selection current
        if not self.current_match:
            # 1. Creates current selection
            self.current_match = list_of_highlights[0]
            self.idx = 0
        else:
        # Case 2: Need to track current selection vs updated matches
            idx, new_current_match = _findNewContainedInterval(list_of_highlights,self.current_match)
            # Case 1:
            if new_current_match:
                self.current_match = new_current_match
                self.idx = idx
            else:
                self.current_match = list_of_highlights[0]
                self.idx = 0

        self.variable_to_print = self.current_match
        self.list_of_highlights = list_of_highlights

    def getCurrentMatch(self):
        return self.variable_to_print
    def incrementIndex(self):
        # EC: no highlights to increment from
        if self.variable_to_print == None:
            return

        # TODO: move in circular buffer?
        self.idx += 1
        if self.idx == len(self.list_of_highlights):
            self.idx = 0
        
        self.current_match = self.list_of_highlights[self.idx]
        self.variable_to_print = self.current_match
    def decrementIndex(self):
        # EC: no highlights to increment from
        if self.variable_to_print == None:
            return

        # TODO: move in circular buffer?
        self.idx -= 1
        if self.idx < 0:
            self.idx = len(self.list_of_highlights) - 1
        
        self.current_match = self.list_of_highlights[self.idx]
        self.variable_to_print = self.current_match



def _findNewContainedInterval(list_of_highlights,current_match):
    for idx,match in enumerate(list_of_highlights):
        # TODO: make this a method so usage is clearer
        if _isContainedIn(current_match,match):
            return idx,match
    return 0,None

def _isContainedIn(current_match,bigger_match):
    if current_match[0] != bigger_match[0]:
        return False

    smaller_range = current_match[1]
    bigger_range = bigger_match[1]
    if bigger_range[0] <= smaller_range[0] and smaller_range[1] <= bigger_range[1]:
        return True
    else:
        return False
################ **Helpers** ##################
def extractWordAndFilters(input,strip_set):
    input = input.split(' ')

    c_word = input[0]
    c_word = CompressedString(c_word,strip_set)

    if len(input) > 1:
        c_filters = [CompressedString(x,strip_set) for x in input[1:]]
    else:
        c_filters = []

    return c_word,c_filters
def findMatches(c_string,c_word,list_of_c_filters=[]):
    """
    Note: match.end()  returns 1 over, just like C++
    """
    matches = _findCWordInCString(c_word,c_string)
    # TODO: search order changes depending on search up or search down
    for c_filter in list_of_c_filters:
        if not _findCWordInCString(c_filter,c_string):
            return []
    return matches

def _findCWordInCString(c_word,c_string):
    return [ x for x in re.finditer(c_word.getString(),c_string.getString())]

def printCurrJumpList(wb_pair,num):
    jump_list1 = wb_pair.vim.call("getjumplist",wb_pair.window)
    # win_info = wb_pair.vim.call("getwininfo",wb_pair.window)
    # DPrintf("Window Info: "+ str(win_info))
    # DPrintf("\n")
    DPrintf("JumpList" + str(num)+": "+ str(jump_list1))


# State Design Pattern to compare with Buffer Variable Implementation
################ **THOUGHTS** ##################
# While there is a lot of boilerplate, the implementation was easier to do
# b/c 1) each state has exactly the variables it needs to consider
# 2) state transitions are explicit rather than being encoded in variables/control flow
# For correctness, I would probably implement this first and then reduce to the version used above

# class HighlightState(object):
    # def update_empty_highlights(self):
        # DPrintf(self.getState()+ ": update_empty_highlights")
        # return self._update_empty_highlights()
    # def update_highlights(self,highlights):
        # DPrintf(self.getState()+ ": update highlights")
        # return self._update_highlights(highlights)
    # def _update_empty_highlights(self):
        # raise NotImplementedError
    # def _update_highlights(self,highlights):
        # raise NotImplementedError
    # def getCurrentMatch(self):
        # raise NotImplementedError
    # def getState(self):
        # raise NotImplementedError

# class NoMatch(HighlightState):
    # def __init__(self):
        # pass
    # def _update_empty_highlights(self):
        # return self
    # def _update_highlights(self,highlights):
        # current_match = highlights[0]
        # return HasMatch(current_match)
    # def getCurrentMatch(self):
        # return None
    # def getState(self):
        # return "NoMatch"

# class HasMatch(HighlightState):
    # def __init__(self,current_match):
        # self.current_match = current_match
    # def _update_empty_highlights(self):
        # return SavedMatch(self.current_match)
    # def _update_highlights(self,highlights):
        # new_current_match = _findNewContainedInterval(highlights,self.current_match)
        # if new_current_match:
            # return HasMatch(new_current_match)
        # else:
            # return HasMatch(highlights[0])
    # def getCurrentMatch(self):
        # return self.current_match
    # def getState(self):
        # return "HasMatch"

# class SavedMatch(HighlightState):
    # def __init__(self,saved_match):
        # self.saved_match = saved_match
    # def _update_empty_highlights(self):
        # return self
    # def _update_highlights(self,highlights):
        # new_current_match = _findNewContainedInterval(highlights,self.saved_match)
        # if new_current_match:
            # return HasMatch(new_current_match)
        # else:
            # return HasMatch(highlights[0])
    # def getCurrentMatch(self):
        # return None
    # def getState(self):
        # return "HasMatch"
