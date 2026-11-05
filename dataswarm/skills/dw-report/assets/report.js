(function () {
  var links = Array.prototype.slice.call(document.querySelectorAll('.toc a'));
  if (links.length) {
    var targets = links.map(function (link) {
      return document.querySelector(link.getAttribute('href'));
    });
    function sync() {
      var y = window.pageYOffset + 120;
      var active = 0;
      for (var i = 0; i < targets.length; i++) {
        if (targets[i] && targets[i].offsetTop <= y) active = i;
      }
      for (var j = 0; j < links.length; j++) {
        links[j].className = j === active ? 'active' : '';
      }
    }
    window.addEventListener('scroll', sync, { passive: true });
    sync();
  }
})();
