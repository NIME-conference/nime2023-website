// Minimal replacement for the Google Sites scripts that were removed from this archived copy.
(function () {
  // sidebar submenus (Program, Call for Submissions): chevron or a label without a link toggles
  document.querySelectorAll('nav li').forEach(function (li) {
    var group = li.querySelector(':scope > [role="group"]');
    var chevron = li.querySelector(':scope > div .j10yRb');
    var label = li.querySelector(':scope > div a');
    if (!group || !chevron) return;
    function toggle(e) {
      e.preventDefault();
      var open = group.style.display !== 'none';
      group.style.display = open ? 'none' : '';
      chevron.querySelector('svg').classList.toggle('FWGjId', !open);
      if (label) label.setAttribute('aria-expanded', String(!open));
    }
    chevron.style.cursor = 'pointer';
    chevron.addEventListener('click', toggle);
    if (label && !label.getAttribute('href')) {
      label.style.cursor = 'pointer';
      label.addEventListener('click', toggle);
    }
  });
  // collapsible sections: header click expands/collapses
  document.querySelectorAll('.qUO6Ue').forEach(function (box) {
    var head = box.querySelector(':scope > .Kzv0Me');
    if (!head) return;
    head.style.cursor = 'pointer';
    head.addEventListener('click', function (e) {
      if (e.target.closest('a[href]')) return;
      box.classList.toggle('vhaaFf');
    });
  });
  // content images (e.g. posters) open at full size in a new tab
  document.querySelectorAll('[role="main"] img').forEach(function (img) {
    if (img.closest('a')) return;
    img.style.cursor = 'zoom-in';
    img.title = 'Open full-size image';
    img.addEventListener('click', function () { window.open(img.currentSrc || img.src, '_blank'); });
  });
  // mobile menu button opens/closes the navigation drawer
  var btn = document.getElementById('s9iPrd');
  var nav = document.getElementById('yuynLe');
  if (btn && nav) {
    btn.addEventListener('click', function () {
      var open = nav.classList.toggle('jsnVQ');
      btn.setAttribute('aria-expanded', String(open));
    });
    document.addEventListener('click', function (e) {
      if (nav.classList.contains('jsnVQ') && !nav.contains(e.target) && !btn.contains(e.target)) {
        nav.classList.remove('jsnVQ');
        btn.setAttribute('aria-expanded', 'false');
      }
    });
  }
})();
