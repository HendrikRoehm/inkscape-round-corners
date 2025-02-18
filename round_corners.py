#!/usr/bin/env python
# coding=utf-8
#
# Copyright (C) 2020 Juergen Weigert, jnweiger@gmail.com
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
# v0.1, 2020-11-08, jw	- initial draught, finding and printing selected nodes to the terminal...
# v0.2, 2020-11-08, jw	- duplicate the selected nodes in their superpaths, write them back.
# v0.3, 2020-11-21, jw	- find "meta-handles"
# v0.4, 2020-11-26, jw	- alpha and trim math added. trimming with a striaght line implemented, needs fixes.
#                         Option 'cut' added.
# v0.5, 2020-11-28, jw	- Cut operation looks correct. Dummy midpoint for large arcs added, looks wrong, of course.
# v1.0, 2020-11-30, jw	- Code completed. Bot cut and arc work fine.
# v1.1, 2020-12-07, jw	- Replaced boolean 'cut' with a method selector 'arc'/'line'. Added round_corners_092.inx
#                         and started backport in round_corners.py -- attempting to run the same code everywhere.
# v1.2, 2020-12-08, jw  - Backporting continued: option parser hack added. Started effect_wrapper() to prepare self.svg
# v1.3, 2020-12-12, jw  - minimalistic compatibility layer for inkscape 0.92.4 done. It now works in both, 1.0 and 0.92!
# v1.4, 2020-12-15, jw  - find_roundable_nodes() added for auto selecting nodes, if none were selected.
#                         And fix https://github.com/jnweiger/inkscape-round-corners/issues/2
#
# Bad side-effect: As the node count increases during operation, the list of
# selected nodes is incorrect afterwards. We have no way to give inkscape an update.
#
"""
Rounded Corners

This extension operates on selected sharp corner nodes and converts them to a fillet (bevel,chamfer).
An arc shaped path segment with the given radius is inserted smoothly.
The fitted arc is approximated by a bezier spline, as we are doing path operations here.
When the sides at the corner are straight lines, the operation never move the sides, it just shortens them to fit the arc.
When the sides are curved, the arc is placed on the tanget line, and the curve may thus change in shape.

Selected smooth nodes are skipped.
Cases with insufficient space (180deg turn or too short segments/handles) are warned about.

This extension is written for inkscape 1.0.1 and is compatible with inkscape 0.92.4 .
The code is 100% new API, but we hook a minimalistic 0.92.4 compatibility layer.
For use with 0.92.4 rename round_corners.092_inx to round_corners.inx and keep this python file as is.

References:
 - https://inkscape.gitlab.io/extensions/documentation/authors/update1.2.html
 - https://gitlab.com/inkscape/extensions/-/wikis/home
 - https://gitlab.com/inkscape/extras/extensions-tutorials/-/blob/master/My-First-Effect-Extension.md
 - https://gitlab.com/inkscape/extensions/-/wikis/uploads/25063b4ae6c3396fcda428105c5cff89/template_effect.zip
 - https://inkscape-extensions-guide.readthedocs.io/en/latest/_modules/inkex/elements.html#ShapeElement.get_path
 - https://inkscape.gitlab.io/extensions/documentation/_modules/inkex/paths.html#CubicSuperPath.to_path

 - https://stackoverflow.com/questions/734076/how-to-best-approximate-a-geometrical-arc-with-a-bezier-curve
 - https://hansmuller-flex.blogspot.com/2011/10/more-about-approximating-circular-arcs.html
 - https://itc.ktu.lt/index.php/ITC/article/download/11812/6479         (Riskus' PDF)

The algorithm of arc_bezier_handles() is based on the approach described in:
A. Riškus, "Approximation of a Cubic Bezier Curve by Circular Arcs and Vice Versa,"
Information Technology and Control, 35(4), 2006 pp. 371-378.
"""

# python2 compatibility:
from __future__ import print_function

import inkex
import os, sys, math, pprint, copy

__version__ = '1.5'             # Keep in sync with round_corners.inx line 16 and line 3
debug = True                   # True: babble on controlling tty

if not hasattr(inkex, 'EffectExtension'):       # START OF INKSCAPE 0.92.X COMPATIBILITY HACK
  """ OOPS, the code **after** this if conditional is meant for inkscape 1.0.1,
      but we seem to be running under inkscape 0.92.x today.
      Well, to make the new code work in the old environment, in here, we do the
      exact oposite of 1.0.1's /usr/share/inkscape/extensions/inkex/deprecated.py
      (which would make old code run in the new 1.0.1 environment.)

      old and new:
      - self.options= {'selected_nodes': ['path1684:0:2', 'path1684:0:0'], 'radius': 2.0, 'ids': ['path1684'], 'method': 'arc'}

      old style:
      - self.document= <lxml.etree._ElementTree object at 0x7f5c2b1a77e8>
      - self.document.getroot() =  <Element {http://www.w3.org/2000/svg}svg at 0x7f5c2b1a78c0>

      new style:
      - self.svg= <class 'inkex.elements._svg.SvgDocumentElement'>
      - self.svg.getElementById('path1684') =  <class 'inkex.elements._polygons.PathElement'>
        ## maybe not even based on an lxml ElephantTree any more? Let's check the new code...
  """
  class MySvgSuperPath(list):
    """ A list (of lists ...) object, that implements a new style to_path() method
        to turn itself into a string attribute for <path d=...> nodes.
    """
    def to_path(self, curves_only=False):
      """ convert from csp [[[[...]]]] to d "m ..."
          using old 0.92.4 api.
          Note that closed paths are not closed properly by formatPath().
          Start and end of a closed path remains as two distinct points that just coincide.
          That is a bug in the old API. Wontfix.
      """
      import cubicsuperpath

      return cubicsuperpath.formatPath(self)


  class MySvgPath():
    def __init__(self, el):
      self.element = el                       # original lxml.etree._Element
      self.d = el.get('d')                    # must exist, else it is not a path :-)
      # print('MySvgPath sodipodi:nodetypes=', el.get('{'+el.nsmap['sodipodi']+'}nodetypes'), file=sys.stderr)
      # print('MySvgPath style=', el.get('style'), file=sys.stderr)
      # print('MySvgPath d=', self.d, file=sys.stderr)

    def to_superpath(self):
      import cubicsuperpath

      # self.d = "m 168.21,78.84 11.44,5.24 -14.65,8.77 z"
      # supp = [[ [[168.21, 78.84], [168.21, 78.84], [168.21, 78.84]],
      #           [[179.65, 84.09], [179.65, 84.09], [179.65, 84.09]],
      #           [[164.99, 92.87], [164.99, 92.87], [164.99, 92.87]],
      #           [[168.21, 78.84], [168.21, 78.84], [168.21, 78.84]] ]]
      return MySvgSuperPath(cubicsuperpath.parsePath(self.d))


  class MySvgElement():
    def __init__(self, el):
      self.element = el                       # original lxml.etree._Element; element.getroottree() has the svg document
      self.tag = el.tag.split('}')[-1]        # strip any namespace prefix. '{http://www.w3.org/2000/svg}path'
      self.id = self.element.attrib.get('id')
      if self.tag == 'path':
        self.path = MySvgPath(el)
      else:
        print("MySvgElement not implemented for <%s id='%s' ..." % (self.tag, self.id), file=sys.stderr)

    def apply_transform(self):
      t = self.element.get('transform')
      # print('MySvgElement transform=', t, file=sys.stderr)
      if t is not None:
        raise(Exception("apply_transform() for id='%s' transform='%s' not impl." % (self.id, t)))

    def set_path(self, d):
      if self.tag != 'path':
        raise(Exception("MySvgElement set_path() called on non-path node" + self.tag))
      if type(d) != type(""):
        raise(Exception("MySvgElement set_path() called with non-string d " + type(d)))
      self.element.set('d', d)


  class MySvgDocumentElement():
    def __init__(self, document):
      self.tree = document
      self.root = document.getroot()
      self.NSS = self.root.nsmap.copy()       # Or should we just use inkex.NSS instead? That has key 'inx', but not 'inkscape' ...
      self.NSS.pop(None)                      # My documents nsmap has cc,svg,inkscape,rdf,sodipodi, and None: http://www.w3.org/2000/svg
      if 'inx' not in self.NSS and 'inkscape' in self.NSS:
        self.NSS['inx'] = self.NSS['inkscape']

    def getElementById(self, id):
      # print("MySvgDocumentElement.getElementById: svg=", self.tree, " svg.root=", self.root, " ID=", id, file=sys.stderr)
      el_list = self.root.xpath('//*[@id="%s"]' % id, namespaces=self.NSS)
      # print("el_list=", el_list, file=sys.stderr)
      if len(el_list) < 1:
        return None
      return MySvgElement(el_list[0])         # Do we need more? document root is accessible via el_list[0].getroottree()


  def compat_add_argument(pars, *args, **kw):
    """ Provide an add_argument() method so that add_argument() can use the new api,
        but implemented in terms of the old api.
    """
    # convert type method into type string as needed, see deprecated.py def add_option()
    if 'type' in kw:
      kw['type'] = { str: 'string', float: 'float', int: 'int', bool: 'inkbool' }.get(kw['type'])
    if 'action' not in kw:
      kw['action'] = 'store'
    pars.add_option(*args, **kw)


  def effect_wrapper(self):
    """ A cheap plastic immitation if inkscape-1.0.1's SvgDocumentElement() class found in
        /usr/share/inkscape/extensions/inkex/elements/_svg.py
        We add an svg object to the old api, so that new style code can run.
        Note: only a very minimal set of methods is supported, and those that are, in a very primitive way.
    """
    self.svg = MySvgDocumentElement(self.document)
    self.wrapped_effect()


  def init_wrapper(self):
    """ To backport the option parsing, we wrap the __init__ method and introduce a compatibility shim.
        we must call add_arguments(), that seems to be done by EffectExtension.__init__() which we don't have.
        have Effect.__init__() instead, which expects to be subclassed. We cannot subclass, as we don't want to
        touch the class code at all. Instead exchange the Effect.__init__() with this wrapper, to hook in
        new style semantics into the old style inkex.Effect superclass.
        We also we must convert from new style pars.add_argument() calls to old style
        self.OptionParser.add_option() -- this is done by the compat_add_argument wrapper.
    """
    from types import MethodType

    self.wrapped_init()                                    # call early, as it adds the OptionParser to self ...

    # We add an add_argument method to the OptionParser. Must do this via MethodType,
    # as direct assignment would discard the indirect object.
    self.OptionParser.add_argument = MethodType(compat_add_argument, self.OptionParser)

    # Now, as the new style add_argument() method is in place, we can run the add_arguments() initializer of the extension.
    self.add_arguments(self.OptionParser)
    self.run = self.affect      # alias the extension entry point so that it works in both APs.

    # wrap our own effect() method. That is ugly, but self.document is not initialized any earlier.
    self.wrapped_effect = self.effect
    self.effect = MethodType(effect_wrapper, self)


  inkex.EffectExtension = inkex.Effect
  inkex.EffectExtension.wrapped_init = inkex.EffectExtension.__init__
  inkex.EffectExtension.__init__ = init_wrapper

# END OF INKSCAPE 0.92.X COMPATIBILITY HACK


max_trim_factor = 0.90          # 0.5: can cut half of a segment length or handle length away for rounding a corner
max_trim_factor_single = 0.98   # 0.98: we can eat up almost everything, as there are no neighbouring trims to be expected.

class RoundedCorners(inkex.EffectExtension):

    def add_arguments(self, pars):              # an __init__ in disguise ...
      try:
        self.tty = open("/dev/tty", 'w')
      except:
        try:
          self.tty = open("CON:", 'w')        # windows. Does this work???
        except:
          self.tty = open(os.devnull, 'w')  # '/dev/null' for POSIX, 'nul' for Windows.
      if debug: print("RoundedCorners ...", file=self.tty)
      self.nodes_inserted = {}
      self.eps = 0.00001                # avoid division by zero
      self.radius = None
      self.max_trim_factor = max_trim_factor

      self.skipped_degenerated = 0      # not a useful corner (e.g. 180deg corner)
      self.skipped_small_count = 0      # not enough room for arc
      self.skipped_small_len = 1e99     # record the shortest handle (or segment) when skipping.

      pars.add_argument("--radius", type=float, default=2.0, help="Radius [mm] to round selected vertices. Default: 2")
      pars.add_argument("--method", type=str, default="arc", help="operation: one of 'arc' (default), 'arc+cross', 'line'")


    def effect(self):
        if debug:
          # SvgInputMixin __init__: "id:subpath:position of selected nodes, if any"
          print(self.options.selected_nodes, file=self.tty)

        self.radius = math.fabs(self.options.radius)
        self.cut = False
        if self.options.method in ('line'):
          self.cut = True
        if len(self.options.selected_nodes) < 1:
          # find selected objects and construct a list of selected_nodes for them...
          for p in self.options.ids:
            self.options.selected_nodes.extend(self.find_roundable_nodes(p))
          if len(self.options.selected_nodes) < 1:
            raise inkex.AbortExtension("Could not find nodes inside a path. No path objects selected?")

        if len(self.options.selected_nodes) == 1:
          # when we only trim one node, we can eat up almost everything,
          # no need to leave room for rounding neighbour nodes.
          self.max_trim_factor = max_trim_factor_single

        for node in sorted(self.options.selected_nodes):
          ## we walk through the list sorted, so that node indices are processed within a subpath in ascending numeric order.
          ## that makes adjusting index offsets after node inserts easier.
          ss = self.round_corner(node)


    def find_roundable_nodes(self, path_id):
      """ select all nodes of all (sub)paths. except for
          - the last (one or two) nodes of a closed path (which coindide with the first node)
          - the first and last node of an open path (which cannot be smoothed)
      """
      ret = []
      elem = self.svg.getElementById(path_id)
      if elem.tag != '{'+elem.nsmap['svg']+'}path':
        return ret      # ellipse never works.
      try:
        csp = elem.path.to_superpath()
      except:
        return ret

      for sp_idx in range(0, len(csp)):
        sp = csp[sp_idx]
        if len(sp) < 3:
          continue      # subpaths of 2 or less nodes are ignored
        if self.very_close(sp[0], sp[-1]):
          idx_s = 0     # closed paths count from 0 to either n-1 or n-2
          idx_e = len(sp) - 1
          if self.very_close_xy(sp[-2][1], sp[-1][1]):
            idx_e = len(sp) - 2
        else:
          idx_s = 1     # open paths count from 1 to either n-1
          idx_e = len(sp) - 1
        for idx in range(idx_s, idx_e):
          ret.append("%s:%d:%d" % (path_id, sp_idx, idx))

        if debug:
          print("find_roundable_nodes: ", self.options.selected_nodes, file=sys.stderr)
      return ret


    def very_close(self, n1, n2):
      "deep compare. all elements in sub arrays are compared for (very close) numerical equality"
      return self.very_close_xy(n1[0], n2[0]) and self.very_close_xy(n1[1], n2[1]) and self.very_close_xy(n1[2], n2[2])


    def very_close_xy(self, p1, p2):
      "one 2 element array is compared for (very close) numerical equality"
      eps = 1e-9
      return abs(p1[0]-p2[0]) < eps and abs(p1[1]-p2[1]) < eps


    def round_corner(self, node_id):
      """ round the corner at (adjusted) node_idx of subpath
          Side_effect: store (or increment) in self.inserted["pathname:subpath"] how many points were inserted in that subpath.
          the adjusted node_idx is computed by adding that number (if exists) to the value of the node_id before doing any manipulation
      """
      s = node_id.split(":")
      path_id = s[0]
      subpath_idx = int(s[1])
      subpath_id = s[0] + ':' + s[1]
      idx_adjust = self.nodes_inserted.get(subpath_id, 0)
      node_idx = int(s[2]) + idx_adjust

      elem = self.svg.getElementById(path_id)
      if elem is None:
        print("selected_node %s not found in svg document" % node_id, file=sys.stderr)
        return None

      elem.apply_transform()       # modifies path inplace? -- We save later back to the same element. Maybe we should not?
      path = elem.path
      s = path.to_superpath()
      sp = s[subpath_idx]

      ## call the actual path manipulator, record how many nodes were inserted.
      orig_len = len(sp)
      sp = self.subpath_round_corner(sp, node_idx)
      idx_adjust += len(sp) - orig_len

      # convert the superpath back to a normal path
      s[subpath_idx] = sp
      elem.set_path(s.to_path(curves_only=False))
      self.nodes_inserted[subpath_id] = idx_adjust


      # If we picked up the 'd' attribute of a non-path (e.g. star), we must make sure the object now becomes a path.
      # Otherwise inkscape uses the sodipodi data and ignores our changed 'd' attribute.
      if '{'+elem.nsmap['sodipodi']+'}type' in elem.attrib:
        del(elem.attrib['{'+elem.nsmap['sodipodi']+'}type'])

      # Debugging is no longer available or not yet implemented? This explodes, although it is
      # documented in https://inkscape.gitlab.io/extensions/documentation/inkex.command.html
      # inkex.command.write_svg(self.svg, "/tmp/seen.svg")
      # - AttributeError: module 'inkex' has no attribute 'command'
      # But hey, we can always resort to good old ET.dump(self.document) ...


    def super_node(self, sp, node_idx):
      """ In case of node_idx 0, we need to use either the last, the second-last or the third last node as a previous node.
          For a closed subpath, the last node and the first node are identical. Then, the second last node may be still at the
          same location if it has a handle. If so, we take the third last instead. Gah. It has a certain logic...

          In case of the node_idx being the last node, we already know that the subpath is not closed,
          we use 0 as the next node.

          The direction sn.prev.dir does not really point to the coordinate of the previous node, but to the end of the
          next-handle of the prvious node. This is the same when there are straight lines. The absence of handles is
          denoted by having the same coordinates for handle and node.
          Same for next.dir, it points to the next.prev handle.

          The exact implementation here is:
          - sn.next.handle is set to a relative vector that is the tangent of the curve towards the next point.
            we implement four cases:
            - if neither node nor next have handles, the connection is a straight line, and next.handle points
              in the direction of the next node itself.
            - if the curve between node and next is defined by two handles, then sn.next.handle is in the direction of the
              nodes own handle,
            - if the curve between node and next is defined one handle at the node itself, then sn.next.handle is in the
              direction of the nodes own handle,
            - if the curve between node and next is defined one handle at the next node, then sn.next.handle is in the
              direction from the node to the end of that other handle.
          - when trimming back later, we move along that tangent, instead of following the curve.
            That is an approximation when the segment is curved, and exact when it is straight.
            (Finding exact candidate points on curved lines that have tangents with the desired circle
            is beyond me today. Multiple candidates may exist. Any volunteers?)
      """

      prev_idx = node_idx - 1
      sp_node_idx_ = copy.deepcopy(sp[node_idx])        # if this wraps around, at node_idx=0, we may need to tweak the prev handle
      if node_idx == 0:
        prev_idx = len(sp) - 1
        if self.very_close(sp_node_idx_, sp[prev_idx]):
          prev_idx = prev_idx - 1       # skip one node, it is the 'close marker'
          if self.very_close_xy(sp_node_idx_[1], sp[prev_idx][1]):
            # still no distance, skip more. Needed for https://github.com/jnweiger/inkscape-round-corners/issues/2
            sp_node_idx_[0] = sp[prev_idx][0]       # this sp_node_idx_ must acts as if its prev handle is that one.
            prev_idx = prev_idx - 1
        else:
          self.skipped_degenerated += 1         # path ends here.
          return None, None

      # if debug: pprint.pprint({'node_idx': node_idx, 'len(sp)':len(sp), 'sp': sp}, stream=self.tty)
      if node_idx == len(sp)-1:
        self.skipped_degenerated += 1           # path ends here. On a closed loop, we can never select the last point.
        return None, None

      next_idx = node_idx + 1
      if next_idx >= len(sp): next_idx = 0
      t = sp_node_idx_
      p = sp[prev_idx]
      n = sp[next_idx]
      dir1 = [ p[2][0] - t[1][0], p[2][1] - t[1][1] ]           # direction to the previous node (rel coords)
      dir2 = [ n[0][0] - t[1][0], n[0][1] - t[1][1] ]           # direction to the next node (rel coords)
      dist1 = math.sqrt(dir1[0]*dir1[0] + dir1[1]*dir1[1])      # distance to the previous node
      dist2 = math.sqrt(dir2[0]*dir2[0] + dir2[1]*dir2[1])      # distance to the next node
      handle1 = [ t[0][0] - t[1][0], t[0][1] - t[1][1] ]        # handle towards previous node (rel coords)
      handle2 = [ t[2][0] - t[1][0], t[2][1] - t[1][1] ]        # handle towards next node (rel coords)
      if self.very_close_xy(handle1, [ 0, 0 ]): handle1 = dir1
      if self.very_close_xy(handle2, [ 0, 0 ]): handle2 = dir2

      prev = { 'idx': prev_idx, 'dir':dir1, 'handle':handle1 }
      next = { 'idx': next_idx, 'dir':dir2, 'handle':handle2 }
      sn = { 'idx': node_idx, 'prev': prev, 'next': next, 'x': t[1][0], 'y': t[1][1] }

      return sn, sp_node_idx_


    def arc_c_m_from_super_node(self, s):
      """
      Given the supernode s and the radius self.radius, we compute and return two points:
      c, the center of the arc and m, the midpoint of the arc.

      Method used:
      - construct the ray c_m_vec that runs though the original point p=[x,y] through c and m.
      - next.trim_pt, [x,y] and c form a rectangular triangle. Thus we can
        compute cdist as the length of the hypothenuses under trim and radius.
      - c is then cdist away from [x,y] along the vector c_m_vec.
      - m is closer to [x,y] than c by exactly radius.
      """

      a = [ s['prev']['trim_pt'][0] - s['x'], s['prev']['trim_pt'][1] - s['y'] ]
      b = [ s['next']['trim_pt'][0] - s['x'], s['next']['trim_pt'][1] - s['y'] ]

      c_m_vec = [ a[0] + b[0],
                  a[1] + b[1] ]
      l = math.sqrt( c_m_vec[0]*c_m_vec[0] + c_m_vec[1]*c_m_vec[1] )

      cdist = math.sqrt( self.radius*self.radius + s['trim']*s['trim'] )    # distance [x,y] to circle center c.

      c = [ s['x'] + cdist * c_m_vec[0] / l,                      # circle center
            s['y'] + cdist * c_m_vec[1] / l ]

      m = [ s['x'] + (cdist-self.radius) * c_m_vec[0] / l,        # spline midpoint
            s['y'] + (cdist-self.radius) * c_m_vec[1] / l ]

      return (c, m)

    def split_bezier_curve(self, p0, p1, p2, p3, t):
      """
      Splits the cubic bezier curve into two parts.

      Based on wikipedia (https://de.wikipedia.org/wiki/B%C3%A9zierkurve#Teilung_einer_B%C3%A9zierkurve).
      Unfortunately, the english wikipedia page does not contain the splitting algorithm.
      """
      t1 = 1-t
      p10 = [t1*p0[0]+t*p1[0], t1*p0[1]+t*p1[1]]
      p11 = [t1*p1[0]+t*p2[0], t1*p1[1]+t*p2[1]]
      p12 = [t1*p2[0]+t*p3[0], t1*p2[1]+t*p3[1]]
      p20 = [t1*p10[0]+t*p11[0], t1*p10[1]+t*p11[1]]
      p21 = [t1*p11[0]+t*p12[0], t1*p11[1]+t*p12[1]]
      p30 = [t1*p20[0]+t*p21[0], t1*p20[1]+t*p21[1]]
      
      return [[p0, p10, p20, p30], [p30, p21, p12, p3]]


    def arc_bezier_handles(self, p1, p4, c):
      """
      Compute the control points p2 and p3 between points p1 and p4, so that the cubic bezier spline
      defined by p1,p2,p3,p2 approximates an arc around center c

      Algorithm based on Aleksas Riškus and Hans Muller. Sorry Pomax, saw your works too, but did not use any.
      """
      x1,y1 = p1
      x4,y4 = p4
      xc,yc = c

      ax = x1 - xc
      ay = y1 - yc
      bx = x4 - xc
      by = y4 - yc
      q1 = ax * ax + ay * ay
      q2 = q1 + ax * bx + ay * by
      k2 = 4./3. * (math.sqrt(2 * q1 * q2) - q2) / (ax * by - ay * bx)

      x2 = xc + ax - k2 * ay
      y2 = yc + ay + k2 * ax
      x3 = xc + bx + k2 * by
      y3 = yc + by - k2 * bx

      return ([x2, y2], [x3, y3])


    def subpath_round_corner(self, sp, node_idx):
      sn, sp_node_idx_ = self.super_node(sp, node_idx)
      if sn is None: return sp          # do nothing. stderr messages are already printed.

      # The angle to be rounded is now between the vectors a and b
      #
      prev_idx = sn['prev']['idx']
      next_idx = sn['next']['idx']

      prev_node = sp[prev_idx]
      next_node = sp[next_idx]
      node = sp[node_idx]

      side = -1 if (node[0][0] - node[1][0])*(node[2][1] - node[1][1]) - (node[0][1] - node[1][1])*(node[2][0] - node[1][0]) > 0 else 1

      s1 = CenterCurveSegment(
        [prev_node[1][0], prev_node[2][0], node[0][0], node[1][0]],
        [prev_node[1][1], prev_node[2][1], node[0][1], node[1][1]],
        side,
        self.radius,
        0.001,
        0,
        1
      )

      s2 = CenterCurveSegment(
        [node[1][0], node[2][0], next_node[0][0], next_node[1][0]],
        [node[1][1], node[2][1], next_node[0][1], next_node[1][1]],
        side,
        self.radius,
        0.001,
        0,
        1
      )

      t = intersectCenterCurveSegments(s1, s2)
      if t is None:
        return sp
      
      arc_c = s1.calculate_center_point(t[0])
      if debug: print(f"center: {arc_c}", file=self.tty)

      [_, p1, p2, p3], _ = self.split_bezier_curve(prev_node[1], prev_node[2], node[0], node[1], t[0])
      _ , [n0, n1, n2, _] = self.split_bezier_curve(node[1], node[2], next_node[0], next_node[1], t[1])

      sp[prev_idx][2] = p1
      sp[next_idx][0] = n2

      c1, c2 = self.arc_bezier_handles(p3, n0, arc_c)

      node_a = [p2, p3, c1]
      node_b = [c2, n0, n1]

      if node_idx == 0:
        # use prev idx to know about the extra skip. +1 for the node here, +1 for inclusive.
        # CAUTION: Keep in sync below
        sp = [node_a] + [node_b] + sp[1:sn['prev']['idx']+2]
      else:
        sp = sp[:node_idx] + [node_a] + [node_b] + sp[node_idx+1:]

      return sp


    def clean_up(self):         # __fini__
      if self.tty is not None:
        self.tty.close()
      super(RoundedCorners, self).clean_up()
      if self.skipped_degenerated:
        print("Warning: Skipped %d degenerated nodes (180° turn or end of path?).\n" % self.skipped_degenerated, file=sys.stderr)
      if self.skipped_small_count:
        print("Warning: Skipped %d nodes with not enough space (Value %g is too small. Try again with a smaller radius or only one node selected).\n" % (self.skipped_small_count, self.skipped_small_len), file=sys.stderr)


class CenterCurveSegment:
  def __init__(self, x, y, side, radius, eps, t_start, t_end, p_start=None, p_end=None):
    """
    x / y list of coordinates of the bezierpoints
    side = 1 -> left side
    side = -1 -> right side
    radius = the radius the corners should have
    eps = allowed errors for numerical calculation of 2d points
    t_start / t_end: defines for which segment of the bezier curve this instance is responsible
    p_start / p_end: Set the points at t_start / t_end if already calculated to mitigate unnecessary calculations
    """
    if len(x) != len(y):
      raise Exception("x and y coordinate lists do not have the same length.")
    if len(x) < 2:
      raise Exception("at least two points required to use the CenterCurveSegment")
    self._x = x
    self._y = y
    self._side = side
    self._radius = radius
    self._eps = eps
    self._t_start = t_start
    self._t_end = t_end
    self._p_start = p_start if p_start is not None else self.calculate_center_point(t_start)
    self._p_end = p_end if p_end is not None else self.calculate_center_point(t_end)

    nx = self._p_end[0]-self._p_start[0]
    ny = self._p_end[1]-self._p_start[1]
    n_norm = math.sqrt(nx**2 + ny**2)
    self._searchDir = (nx / n_norm, ny / n_norm)

    # check that the line between p_start and p_end approximates the curve good enough
    t_mid = (t_start+t_end)/2.
    p_mid = self.calculate_center_point(t_mid)
    p_mid_est = ((self._p_start[0]+self._p_end[0])/2, (self._p_start[1]+self._p_end[1])/2)
    if  self._t_end - self._t_start > 0.26 or (p_mid_est[0] - p_mid[0])**2 + (p_mid_est[1] - p_mid[1])**2 > self._eps**2:
      # approximation is not good enough yet
      self._segments = [
        CenterCurveSegment(
          self._x,
          self._y,
          self._side,
          self._radius,
          self._eps,
          self._t_start,
          t_mid,
          self._p_start,
          p_mid
        ),
        CenterCurveSegment(
          self._x,
          self._y,
          self._side,
          self._radius,
          self._eps,
          t_mid,
          self._t_end,
          p_mid,
          self._p_end,
        )
      ]
      self._terminalSegments = sum([s._terminalSegments for s in self._segments])
      searchValues = self._segments[0].convexHullSearchValues(self._searchDir)
      for s in self._segments[1:]:
        localSearchValues = s.convexHullSearchValues(self._searchDir)
        searchValues = (
          min(searchValues[0], localSearchValues[0]),
          max(searchValues[1], localSearchValues[1]),
          min(searchValues[2], localSearchValues[2]),
          max(searchValues[3], localSearchValues[3])
        )
      self._searchValues = searchValues
      # calculate hull points from the intersectSearchValues
      # this is an overapproximation for the curve, but can be computed recursively and fast
      self._hullPoints = [
        (
          self._searchDir[0]*self._searchValues[0]-self._searchDir[1]*self._searchValues[2],
          self._searchDir[1]*self._searchValues[0]+self._searchDir[0]*self._searchValues[2],
        ),
        (
          self._searchDir[0]*self._searchValues[0]-self._searchDir[1]*self._searchValues[3],
          self._searchDir[1]*self._searchValues[0]+self._searchDir[0]*self._searchValues[3],
        ),
        (
          self._searchDir[0]*self._searchValues[1]-self._searchDir[1]*self._searchValues[2],
          self._searchDir[1]*self._searchValues[1]+self._searchDir[0]*self._searchValues[2],
        ),

        (
          self._searchDir[0]*self._searchValues[1]-self._searchDir[1]*self._searchValues[3],
          self._searchDir[1]*self._searchValues[1]+self._searchDir[0]*self._searchValues[3],
        ),
      ]
    else:
      self._segments = None
      self._terminalSegments = 1
      self._hullPoints = [self._p_start, self._p_end]
      self._searchValues = self.convexHullSearchValues(self._searchDir)

  def convexHullSearchValues(self, searchDir):
    v_parallel = [searchDir[0]*p[0]+searchDir[1]*p[1] for p in self._hullPoints]
    v_orthogonal = [-searchDir[1]*p[0]+searchDir[0]*p[1] for p in self._hullPoints]
    return (min(v_parallel), max(v_parallel), min(v_orthogonal), max(v_orthogonal))

  def calculate_center_point(self, t):
    prev_x_list = self._x
    prev_y_list = self._y

    while len(prev_x_list) > 2:
      # use de casteljaus algorithm to compute points on a bezier curve
      prev_x = prev_x_list[0]
      prev_y = prev_y_list[0]
      x_list = []
      y_list = []
      i = 1
      while i < len(prev_x_list):
        x = prev_x_list[i]
        y = prev_y_list[i]
        x_list.append((1-t)*prev_x + t*x)
        y_list.append((1-t)*prev_y + t*y)
        prev_x = x
        prev_y = y
        i += 1
      
      prev_x_list = x_list
      prev_y_list = y_list

    dx = prev_x_list[1] - prev_x_list[0]
    dy = prev_y_list[1] - prev_y_list[0]
    norm = math.sqrt(dx**2+dy**2)

    # TODO: handle norm=0 case -> need to check the second derivative for the direction
    cx = (1-t)*prev_x_list[0] + t*prev_x_list[1] - self._side * dy / norm * self._radius
    cy = (1-t)*prev_y_list[0] + t*prev_y_list[1] + self._side * dx / norm * self._radius

    return (cx, cy)

def intersectCenterCurveSegments(segment1, segment2):
  searchValues = segment2.convexHullSearchValues(segment1._searchDir)
  if segment1._searchValues[1] < searchValues[0] or segment1._searchValues[0] > searchValues[1] or segment1._searchValues[3] < searchValues[2] or segment1._searchValues[2] > searchValues[3]:
    return None
  
  searchValues = segment1.convexHullSearchValues(segment2._searchDir)
  if segment2._searchValues[1] < searchValues[0] or segment2._searchValues[0] > searchValues[1] or segment2._searchValues[3] < searchValues[2] or segment2._searchValues[2] > searchValues[3]:
    return None
  
  if segment1._terminalSegments == 1 and segment2._terminalSegments == 1:
    # nothing to divide here anymore. Just calculate the time of the intersection of the lines
    a11 = segment1._p_end[0] - segment1._p_start[0]
    a21 = segment1._p_end[1] - segment1._p_start[1]
    a12 = segment2._p_start[0] - segment2._p_end[0]
    a22 = segment2._p_start[1] - segment2._p_end[1]
    b1 = segment2._p_start[0] - segment1._p_start[0]
    b2 = segment2._p_start[1] - segment1._p_start[1]
    det = a11*a22 - a21*a12
    print(segment1._t_start, segment1._t_end)
    if det != 0.:
      t1 = (a22*b1 - a12*b2)/det
      t2 = (-a21*b1 + a11*b2)/det
      return (
        (1-t1) * segment1._t_start + t1 * segment1._t_end,
        (1-t2) * segment2._t_start + t2 * segment2._t_end,
      )
    else:
      # TODO: Implement det = 0 case
      raise Exception("det = 0 case not implemented yet")

  subSegments1 = segment1._segments if segment1._terminalSegments > 1 else [segment1]
  # reverse as we want to find the last matching for segment1
  subSegments1.reverse()
  subSegments2 = segment2._segments if segment2._terminalSegments > 1 else [segment2]

  for s1 in subSegments1:
    for s2 in subSegments2:
      ret = intersectCenterCurveSegments(s1, s2)
      if ret is not None:
        return ret

  return None

if __name__ == '__main__':
    RoundedCorners().run()
