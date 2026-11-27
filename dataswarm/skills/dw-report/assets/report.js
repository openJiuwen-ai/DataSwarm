// Presentation only: series data and evidence keep their original precision.
function dwReportFormatValue(value) {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return value.map(dwReportFormatValue).join(', ');
  var suffix = '';
  if (typeof value === 'string') {
    var match = value.trim().match(/^[+-]?(?:(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)\.\d+(?:[eE][+-]?\d+)?|(?:0|[1-9]\d*)[eE][+-]?\d+)(%|％)?$/);
    if (!match) return value;
    suffix = match[1] || '';
    value = Number(value.trim().replace(/[%％,]/g, ''));
  }
  if (typeof value !== 'number') return String(value);
  if (!isFinite(value)) return '—';
  if (value === 0) return '0' + suffix;
  if (Math.abs(value) < 0.005) {
    var small = Number(value.toPrecision(3));
    return (Math.abs(small) < 1e-18 ? String(small) :
      small.toLocaleString('en-US', { maximumFractionDigits: 20 })) + suffix;
  }
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 }) + suffix;
}

function dwReportFormatChartOption(option) {
  if (!option || typeof option !== 'object') return;
  function each(value, callback) {
    (Array.isArray(value) ? value : value ? [value] : []).forEach(callback);
  }
  function tooltip(value) {
    if (value && value.valueFormatter === undefined) value.valueFormatter = dwReportFormatValue;
  }
  if (!option.tooltip) option.tooltip = {};
  each(option.tooltip, tooltip);
  ['xAxis', 'yAxis', 'radiusAxis', 'singleAxis'].forEach(function (key) {
    each(option[key], function (axis) {
      if (axis.type && axis.type !== 'value' && axis.type !== 'log') return;
      if (!axis.type && (axis.data || key === 'xAxis')) return;
      if (!axis.axisLabel) axis.axisLabel = {};
      if (axis.axisLabel.formatter === undefined) axis.axisLabel.formatter = dwReportFormatValue;
    });
  });
  each(option.series, function (series) {
    each(series.tooltip, tooltip);
    if (['bar', 'line', 'scatter', 'heatmap'].indexOf(series.type) < 0) return;
    if (!series.label) series.label = {};
    if (series.label.formatter === undefined) {
      series.label.formatter = function (params) {
        var value = params.value;
        if (Array.isArray(value)) value = value[value.length - 1];
        return dwReportFormatValue(value);
      };
    }
  });
  dwReportFormatChartOption(option.baseOption);
  each(option.options, dwReportFormatChartOption);
  each(option.media, function (item) { dwReportFormatChartOption(item.option); });
}

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
