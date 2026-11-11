/* SPDX-License-Identifier: MPL-2.0 · Copyright © 2026 lucya.systems — https://lucya.systems */
/* ==================================================================
   NEBULA 2.2.0  —  the keyboard, for the parts that need one
   ------------------------------------------------------------------
   nebula.css draws the controls; this makes the composite ones work
   from a keyboard the way the ARIA patterns say they should. Tab
   reaches a GROUP once, and the arrow keys move inside it. Without it,
   a row of eight segments is eight tab stops and a menu is a list you
   have to Tab through and out of.

   Copy it next to nebula.css, same version, and load it deferred. No
   dependencies, no inline styles, nothing to configure: it finds its
   parts by their ARIA roles, which the markup needs anyway.

     [role=tablist]    > [role=tab]     Left/Right (Up/Down when
                                        aria-orientation=vertical),
                                        Home, End. The tab that gets
                                        focus is activated, by clicking
                                        it, so the app's own click
                                        handler stays the only one.
     [role=radiogroup] > [role=radio]   The same keys; selection follows
                                        focus, as native radios do.
                                        aria-checked is kept in step
                                        with clicks, and .seg paints it.
     [role=toolbar]                     The same keys, focus only, no
                                        selection.
     .menu[popover]                     Opens with focus on the selected
                                        option (or the first); Up/Down,
                                        Home, End move; a letter jumps to
                                        the next option starting with it;
                                        Tab closes it, and focus goes on.

   All of them use a ROVING tabindex: the current item is 0, the rest
   -1, so Tab enters the group at the item that matters and leaves it
   with the next Tab. Disabled items are skipped.

   Content added later: call NebulaKeys.init(root) on it.
   ================================================================== */
(function () {
  'use strict';

  var VERSION = '2.2.0';
  var GROUPS = '[role="tablist"], [role="radiogroup"], [role="toolbar"]';
  var ITEM = { tablist: '[role="tab"]', radiogroup: '[role="radio"]', toolbar: 'button, a[href], input, [tabindex]' };

  function items(group) {
    var kind = group.getAttribute('role');
    return Array.prototype.filter.call(group.querySelectorAll(ITEM[kind]), function (el) {
      /* only this group's own items, not those of a group nested in it */
      return el.closest(GROUPS) === group && !el.disabled && el.getAttribute('aria-disabled') !== 'true' && !el.hidden;
    });
  }

  function isOn(el) {
    return el.getAttribute('aria-selected') === 'true' || el.getAttribute('aria-checked') === 'true'
      || el.classList.contains('is-on') || el.classList.contains('is-active');
  }

  /* one item is reachable by Tab: the selected one, else the first */
  function rove(group, current) {
    var list = items(group);
    if (!list.length) return;
    var target = current || list.filter(isOn)[0] || list[0];
    list.forEach(function (el) { el.setAttribute('tabindex', el === target ? '0' : '-1'); });
  }

  /* a radio's checked state, kept in step whichever way it was chosen */
  function check(group, chosen) {
    items(group).forEach(function (el) { el.setAttribute('aria-checked', String(el === chosen)); });
  }

  function step(list, from, key, vertical) {
    var i = list.indexOf(from);
    var next = vertical ? 'ArrowDown' : 'ArrowRight', prev = vertical ? 'ArrowUp' : 'ArrowLeft';
    /* a radiogroup answers to both axes, as native radios do */
    if (!vertical && (key === 'ArrowDown')) key = next;
    if (!vertical && (key === 'ArrowUp')) key = prev;
    if (key === next) return list[(i + 1) % list.length];
    if (key === prev) return list[(i - 1 + list.length) % list.length];
    if (key === 'Home') return list[0];
    if (key === 'End') return list[list.length - 1];
    return null;
  }

  function wireGroup(group) {
    if (group.dataset.nebulaKeys) return;
    group.dataset.nebulaKeys = '1';
    var kind = group.getAttribute('role');
    if (kind === 'radiogroup') {
      /* first paint: aria-checked from whatever the markup marked as on */
      var on = items(group).filter(isOn)[0];
      if (on) check(group, on);
    }
    rove(group);

    group.addEventListener('keydown', function (e) {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      var list = items(group);
      var from = e.target.closest(ITEM[kind]);
      if (!from || list.indexOf(from) < 0) return;
      var vertical = group.getAttribute('aria-orientation') === 'vertical';
      var axisKeys = kind === 'radiogroup' ? ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown']
        : vertical ? ['ArrowUp', 'ArrowDown'] : ['ArrowLeft', 'ArrowRight'];
      if (axisKeys.indexOf(e.key) < 0 && e.key !== 'Home' && e.key !== 'End') return;
      var to = step(list, from, e.key, vertical && kind !== 'radiogroup');
      if (!to) return;
      e.preventDefault();
      rove(group, to);
      to.focus();
      /* tabs and radios: focus selects, through the app's own handler */
      if (kind !== 'toolbar' && !isOn(to)) to.click();
    });

    group.addEventListener('click', function (e) {
      var hit = e.target.closest(ITEM[kind]);
      if (!hit || items(group).indexOf(hit) < 0) return;
      if (kind === 'radiogroup') check(group, hit);
      rove(group, hit);
    });
  }

  /* the next (or previous) element Tab would reach, from `el` */
  var TABBABLE = 'a[href], button, input, select, textarea, summary, [tabindex]';
  function moveFrom(el, dir) {
    var all = Array.prototype.filter.call(document.querySelectorAll(TABBABLE), function (x) {
      return x.tabIndex >= 0 && !x.disabled && x.getClientRects().length > 0 && !x.closest('[popover]:not(:popover-open), [inert], [hidden]');
    });
    var i = all.indexOf(el);
    var to = all[i + dir] || el;
    to.focus();
  }

  function options(menu) {
    return Array.prototype.filter.call(menu.querySelectorAll('.menu__option'), function (el) {
      return !el.disabled && el.getAttribute('aria-disabled') !== 'true' && !el.hidden;
    });
  }

  function wireMenu(menu) {
    if (menu.dataset.nebulaKeys) return;
    menu.dataset.nebulaKeys = '1';
    if (!menu.getAttribute('role')) menu.setAttribute('role', 'menu');
    options(menu).forEach(function (o) {
      if (!o.getAttribute('role')) o.setAttribute('role', 'menuitemradio');
      o.setAttribute('tabindex', '-1');
    });
    /* role="menuitemradio" speaks aria-checked; keep it beside the class */
    var sync = function () {
      options(menu).forEach(function (o) {
        o.setAttribute('aria-checked', String(o.classList.contains('is-active') || o.getAttribute('aria-selected') === 'true'));
      });
    };
    sync();

    menu.addEventListener('toggle', function (e) {
      if (e.newState !== 'open') return;
      sync();
      var list = options(menu);
      var start = list.filter(function (o) { return o.getAttribute('aria-checked') === 'true'; })[0] || list[0];
      /* directly: `toggle` already fires after the menu is shown, and a
         frame callback would not run at all in a background tab */
      if (start) start.focus();
    });

    menu.addEventListener('click', function () { setTimeout(sync, 0); });

    menu.addEventListener('keydown', function (e) {
      var list = options(menu);
      var i = list.indexOf(document.activeElement);
      /* a key that lands before focus has moved in counts from the checked
         option, not from before the first */
      if (i < 0) i = Math.max(0, list.findIndex(function (o) { return o.getAttribute('aria-checked') === 'true'; }));
      var to = null;
      if (e.key === 'ArrowDown') to = list[(i + 1) % list.length];
      else if (e.key === 'ArrowUp') to = list[(i - 1 + list.length) % list.length];
      else if (e.key === 'Home') to = list[0];
      else if (e.key === 'End') to = list[list.length - 1];
      else if (e.key === 'Tab') {
        /* Tab leaves the menu as if it had been left from its button: the
           menu closes and focus goes to whatever comes after (or before,
           with Shift) the button — not to the top of the page, which is
           where a closing popover drops it otherwise */
        e.preventDefault();
        var invoker = document.querySelector('[popovertarget="' + menu.id + '"]');
        if (menu.hidePopover) menu.hidePopover();
        if (invoker) moveFrom(invoker, e.shiftKey ? -1 : 1);
        return;
      }
      else if (e.key.length === 1 && /\S/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) {
        /* type-ahead: the next option whose label starts with the letter */
        var ch = e.key.toLowerCase();
        for (var k = 1; k <= list.length; k++) {
          var o = list[(i + k) % list.length];
          if (o.textContent.trim().toLowerCase().indexOf(ch) === 0) { to = o; break; }
        }
      }
      if (!to) return;
      e.preventDefault();
      to.focus();
    });
  }

  function init(root) {
    root = root || document;
    Array.prototype.forEach.call(root.querySelectorAll(GROUPS), wireGroup);
    Array.prototype.forEach.call(root.querySelectorAll('.menu[popover]'), wireMenu);
  }

  window.NebulaKeys = { version: VERSION, init: init };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { init(); });
  else init();
})();
