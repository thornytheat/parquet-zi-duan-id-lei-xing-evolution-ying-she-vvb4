// 原生 JS：分段切换 + 工作台执行对比 + 样例载入 + 复核筛选 + SQL 复制；无框架、无打包器。
(function () {
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  // ---- 1) 分段控件：[data-tab-target] → 同面板内 .tab-panel 只显示目标 ----
  qsa('[data-tab-target]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var group = btn.closest('.segmented') || btn.closest('.tabs');
      if (group) {
        qsa('button', group).forEach(function (b) { b.classList.remove('active'); });
      }
      btn.classList.add('active');
      var target = document.getElementById(btn.getAttribute('data-tab-target'));
      if (!target) return;
      var host = btn.closest('.panel') || document;
      qsa('.tab-panel', host).forEach(function (panel) { panel.hidden = (panel !== target); });
      var workspace = qs('.workspace');
      if (workspace) workspace.scrollTop = 0;
    });
  });

  // ---- 2) 统一 POST JSON ----
  window.postJSON = async function (url, payload) {
    var res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {})
    });
    var data = null;
    try { data = await res.json(); } catch (e) { data = { detail: '响应不是 JSON' }; }
    if (!res.ok) {
      var msg = (data && (data.detail || data.message)) || ('请求失败：' + res.status);
      throw new Error(msg);
    }
    return data;
  };

  window.showError = function (host, err) {
    if (!host) return;
    host.hidden = false;
    host.textContent = '执行失败：' + (err && err.message ? err.message : String(err));
  };

  // ---- 3) 工作台：载入样例 + 执行对比 ----
  var runBtn = qs('#btn-run');
  if (runBtn) {
    var oldEl = qs('#old-schema');
    var newEl = qs('#new-schema');
    var consEl = qs('#consumers');
    var errBox = qs('#error-box');
    var okBox = qs('#ok-box');
    var sampleSel = qs('#sample-select');
    var sampleHint = qs('#sample-hint');
    var loadBtn = qs('#btn-load');

    function hideBoxes() { if (errBox) errBox.hidden = true; if (okBox) okBox.hidden = true; }

    if (loadBtn && sampleSel && oldEl && newEl && consEl) {
      loadBtn.addEventListener('click', async function () {
        hideBoxes();
        var key = sampleSel.value;
        try {
          var res = await fetch('/api/samples/' + key);
          var data = await res.json();
          if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
          oldEl.value = data.old;
          newEl.value = data.new;
          consEl.value = data.consumers;
          if (sampleHint) sampleHint.textContent = data.description + '（预期：' + data.expected + '）';
          if (okBox) {
            okBox.hidden = false;
            okBox.textContent = '已载入样例 ' + key + '：' + data.label;
          }
        } catch (err) {
          if (errBox) window.showError(errBox, err);
        }
      });
    }

    runBtn.addEventListener('click', async function () {
      hideBoxes();
      runBtn.disabled = true;
      runBtn.textContent = '计算中…';
      try {
        var payload = {
          old: oldEl ? oldEl.value : '',
          new: newEl ? newEl.value : '',
          consumers: consEl ? consEl.value : ''
        };
        var data = await window.postJSON('/api/compare', payload);
        if (okBox) {
          okBox.hidden = false;
          okBox.textContent = '对比完成：' + data.summary.level_label + '，ALTER ' +
            data.summary.sql_steps + ' 条，broken_consumers ' + data.summary.broken_consumers + ' 处。正在跳转结果页…';
        }
        window.location.href = data.redirect;
      } catch (err) {
        if (errBox) window.showError(errBox, err);
      } finally {
        runBtn.disabled = false;
        runBtn.textContent = '执行对比';
      }
    });

    if (sampleSel) {
      sampleSel.addEventListener('change', function () {
        hideBoxes();
        if (sampleHint) sampleHint.textContent = '已切换样例，点「载入该样例」填充 JSON；右侧预演仍按当前页面载入的样例渲染。';
      });
    }
  }

  // ---- 4) 复核页：关键字搜索 + 兼容级别筛选（纯前端，不新增接口）----
  var reviewSearch = qs('#review-search');
  var reviewLevel = qs('#review-level');
  var runsTable = qs('#runs-table');
  if (runsTable && (reviewSearch || reviewLevel)) {
    var stat = qs('#review-stat');
    var rows = qsa('tbody tr', runsTable);
    var total = rows.length;
    var applyFilter = function () {
      var needle = (reviewSearch ? reviewSearch.value : '').trim().toLowerCase();
      var level = reviewLevel ? reviewLevel.value : '';
      var shown = 0;
      rows.forEach(function (tr) {
        var haystack = ((tr.getAttribute('data-search') || '') + ' ' + tr.textContent).toLowerCase();
        var hitText = !needle || haystack.indexOf(needle) !== -1;
        var hitLevel = !level || tr.getAttribute('data-level') === level;
        var hit = hitText && hitLevel;
        tr.hidden = !hit;
        if (hit) shown += 1;
      });
      if (stat) {
        var active = needle || level;
        stat.textContent = active ? ('命中 ' + shown + ' / ' + total + ' 条') : ('本页 ' + total + ' 条');
      }
    };
    if (reviewSearch) {
      reviewSearch.addEventListener('input', applyFilter);
      reviewSearch.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') { reviewSearch.value = ''; applyFilter(); }
      });
    }
    if (reviewLevel) reviewLevel.addEventListener('change', applyFilter);
    applyFilter();
  }

  // ---- 5) 结果页：复制 SQL ----
  var sqlBlock = qs('#sql-block');
  if (sqlBlock && navigator.clipboard) {
    var copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.textContent = '复制 SQL';
    copyBtn.className = 'secondary small';
    copyBtn.style.marginTop = '8px';
    copyBtn.addEventListener('click', function () {
      navigator.clipboard.writeText(sqlBlock.textContent).then(function () {
        copyBtn.textContent = '已复制';
        setTimeout(function () { copyBtn.textContent = '复制 SQL'; }, 1500);
      });
    });
    sqlBlock.parentNode.insertBefore(copyBtn, sqlBlock.nextSibling);
  }
})();
