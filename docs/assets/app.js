/* ===========================================================================
   The Daily Brief - client
   Reads docs/data/latest.json (or an archived edition) and renders the feed.
   No framework, no build step, no external requests.
   =========================================================================== */

(function () {
  'use strict';

  /* ------------------------------------------------------------- i18n ---- */

  var I18N = {
    en: {
      title: 'The Daily Brief',
      pageTitle: 'The Daily Brief — World, tech, business & science headlines',
      tagline: 'Trusted world, tech, business and science headlines, every morning.',
      topStories: 'Top stories',
      allNews: 'All news',
      all: 'All',
      searchPlaceholder: 'Search headlines, sources…',
      allSources: 'All sources',
      latestEdition: 'Latest edition',
      updated: 'Updated',
      justNow: 'just now',
      minutesAgo: '{n} min ago',
      hoursAgo: '{n}h ago',
      daysAgo: '{n}d ago',
      read: 'Read',
      newTab: 'opens in a new tab',
      loadMore: 'Load 24 more',
      shownOf: '{shown} of {total} stories',
      archiveNote: 'You are viewing an archived edition from {date}.',
      backToLatest: 'Back to latest',
      noResults: 'No stories match your filters',
      noResultsText: 'Nothing matched “{q}”. Try another keyword, or reset the filters to browse all {n} stories.',
      noResultsTextPlain: 'Try a different keyword or source, or reset the filters to browse all {n} stories.',
      resetFilters: 'Reset filters',
      loadError: 'Could not load the news data',
      loadErrorText: 'The file data/latest.json could not be fetched. If you opened this page directly from disk, serve it over HTTP instead — for example: python3 -m http.server 8000 — then open http://localhost:8000.',
      retry: 'Try again',
      statStories: 'Stories',
      statSources: 'Sources',
      statFeeds: 'Feeds',
      statBuild: 'Built',
      seconds: 's',
      footerNote: 'Every headline is pulled from the publisher’s own public RSS feed and links straight back to the original article. All content and rights belong to the respective publishers.',
      mtNote: 'Chinese headlines and summaries are machine translated for convenience only — always check the original article for accuracy.',
      feedsOk: '{ok}/{total} feeds responded',
      themeLabel: 'Switch theme'
    },
    zh: {
      title: '每日简报',
      pageTitle: '每日简报 — 全球要闻 · 科技 · 财经 · 科学',
      tagline: '每天早上汇总全球要闻、科技、财经与科学头条。',
      topStories: '今日要闻',
      allNews: '全部新闻',
      all: '全部',
      searchPlaceholder: '搜索标题、来源…',
      allSources: '全部来源',
      latestEdition: '最新一期',
      updated: '更新于',
      justNow: '刚刚',
      minutesAgo: '{n} 分钟前',
      hoursAgo: '{n} 小时前',
      daysAgo: '{n} 天前',
      read: '阅读原文',
      newTab: '在新标签页打开',
      loadMore: '加载更多 24 条',
      shownOf: '已显示 {shown} / {total} 条',
      archiveNote: '你正在查看 {date} 的历史存档。',
      backToLatest: '返回最新一期',
      noResults: '没有符合条件的新闻',
      noResultsText: '没有匹配 “{q}” 的新闻。换个关键词，或重置筛选条件以浏览全部 {n} 条。',
      noResultsTextPlain: '换个关键词或来源，或重置筛选条件以浏览全部 {n} 条新闻。',
      resetFilters: '重置筛选',
      loadError: '新闻数据加载失败',
      loadErrorText: '无法读取 data/latest.json。如果你是从磁盘直接打开该页面的，请改用本地 HTTP 服务访问：python3 -m http.server 8000，然后打开 http://localhost:8000。',
      retry: '重试',
      statStories: '条新闻',
      statSources: '个来源',
      statFeeds: '源可用',
      statBuild: '耗时',
      seconds: ' 秒',
      footerNote: '所有标题均来自各媒体公开发布的 RSS 源，并直接链接回原文。内容与版权归各出版方所有。',
      mtNote: '中文标题与摘要由机器翻译自动生成，仅供参考；准确内容请以英文原文为准。',
      feedsOk: '{ok}/{total} 个源响应正常',
      themeLabel: '切换主题'
    }
  };

  var PAGE_SIZE = 24;
  var moreObserver = null;
  var loadingMore = false;
  var LS = { lang: 'db.lang', theme: 'db.theme', cat: 'db.category' };

  /* ------------------------------------------------------------ state ---- */

  var state = {
    lang: read(LS.lang) || (navigator.language || '').toLowerCase().indexOf('zh') === 0 ? read(LS.lang) || 'zh' : 'en',
    theme: read(LS.theme) || null,
    category: read(LS.cat) || 'all',
    query: '',
    source: 'all',
    edition: 'latest',
    data: null,
    index: [],
    pool: [],
    shown: 0,
    catMeta: {}
  };

  /* ----------------------------------------------------------- helpers --- */

  function $(id) { return document.getElementById(id); }

  function read(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }

  function t(key, vars) {
    var dict = I18N[state.lang] || I18N.en;
    var out = dict[key] != null ? dict[key] : (I18N.en[key] != null ? I18N.en[key] : key);
    if (vars) {
      Object.keys(vars).forEach(function (k) {
        out = out.replace(new RegExp('\\{' + k + '\\}', 'g'), vars[k]);
      });
    }
    return out;
  }

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function safeUrl(url) {
    return /^https?:\/\//i.test(url || '') ? url : '';
  }

  function hexToRgb(hex) {
    var m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || '');
    if (!m) return '37, 99, 235';
    return parseInt(m[1], 16) + ', ' + parseInt(m[2], 16) + ', ' + parseInt(m[3], 16);
  }

  function relativeTime(iso) {
    if (!iso) return '';
    var then = new Date(iso);
    if (isNaN(then.getTime())) return '';
    var mins = Math.round((Date.now() - then.getTime()) / 60000);
    if (mins < 1) return t('justNow');
    if (mins < 60) return t('minutesAgo', { n: mins });
    var hours = Math.round(mins / 60);
    if (hours < 24) return t('hoursAgo', { n: hours });
    return t('daysAgo', { n: Math.round(hours / 24) });
  }

  function absoluteTime(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString(state.lang === 'zh' ? 'zh-CN' : 'en-GB', {
      dateStyle: 'medium', timeStyle: 'short'
    });
  }

  function catLabel(article) {
    var meta = state.catMeta[article.category];
    if (!meta) return article.category;
    return state.lang === 'zh' ? meta.zh : meta.en;
  }

  function catAccent(article) {
    var meta = state.catMeta[article.category];
    return (meta && meta.accent) || '#2563eb';
  }

  function localized(article, field) {
    if (state.lang === 'zh') {
      var zh = article[field + '_zh'];
      if (zh && zh.trim()) return zh;
    }
    return article[field] || '';
  }

  function isTranslated(article, field) {
    var zh = article[field + '_zh'];
    var en = article[field];
    return state.lang === 'zh' && !!zh && zh.trim() !== '' && zh !== en;
  }

  /* -------------------------------------------------------------- theme -- */

  function applyTheme(theme) {
    state.theme = theme;
    document.documentElement.setAttribute('data-theme', theme);
    write(LS.theme, theme);
    var btn = $('themeToggle');
    if (btn) btn.setAttribute('aria-label', t('themeLabel'));
  }

  /* --------------------------------------------------------------- i18n -- */

  function applyI18n() {
    document.documentElement.lang = state.lang === 'zh' ? 'zh-CN' : 'en';
    document.title = t('pageTitle');

    var nodes = document.querySelectorAll('[data-i18n]');
    for (var i = 0; i < nodes.length; i++) {
      var key = nodes[i].getAttribute('data-i18n');
      nodes[i].textContent = t(key);
    }
    var attrs = document.querySelectorAll('[data-i18n-attr]');
    for (var j = 0; j < attrs.length; j++) {
      var parts = attrs[j].getAttribute('data-i18n-attr').split(':');
      attrs[j].setAttribute(parts[0], t(parts[1]));
    }
    var segs = document.querySelectorAll('.seg__btn');
    for (var k = 0; k < segs.length; k++) {
      segs[k].classList.toggle('is-on', segs[k].getAttribute('data-lang') === state.lang);
    }
  }

  /* --------------------------------------------------------------- data -- */

  function dataUrl(edition) {
    return edition === 'latest'
      ? 'data/latest.json'
      : 'data/archive/' + edition + '.json';
  }

  function fetchJson(url) {
    return fetch(url, { cache: 'no-cache' }).then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    });
  }

  function load(edition) {
    showSkeleton(true);
    hideState();
    $('hero').hidden = true;

    return fetchJson(dataUrl(edition))
      .then(function (data) {
        state.data = data;
        state.edition = edition;
        state.catMeta = {};
        (data.categories || []).forEach(function (cat) {
          state.catMeta[cat.id] = cat;
        });
        state.pool = (data.articles || []).slice().sort(function (a, b) {
          return String(b.published || '').localeCompare(String(a.published || ''));
        });
        if (state.source !== 'all') {
          var alive = state.pool.some(function (a) { return a.source_id === state.source; });
          if (!alive) state.source = 'all';
        }
        showSkeleton(false);
        buildControls();
        renderChrome();
        render();
      })
      .catch(function (err) {
        showSkeleton(false);
        showState('error', t('loadError'), t('loadErrorText'), t('retry'), function () { load(edition); });
        if (window.console) console.error('[daily-brief]', err);
      });
  }

  function loadIndex() {
    return fetchJson('data/index.json')
      .then(function (idx) { state.index = (idx && idx.dates) || []; })
      .catch(function () { state.index = []; });
  }

  /* ----------------------------------------------------------- controls -- */

  function buildControls() {
    var counts = {};
    state.pool.forEach(function (a) {
      counts[a.category] = (counts[a.category] || 0) + 1;
    });

    var tabs = [{ id: 'all', en: t('all'), zh: t('all'), count: state.pool.length }];
    (state.data.categories || []).forEach(function (cat) {
      if (counts[cat.id]) tabs.push({ id: cat.id, en: cat.en, zh: cat.zh, count: counts[cat.id] });
    });

    $('tabs').innerHTML = tabs.map(function (tab) {
      var label = state.lang === 'zh' ? tab.zh : tab.en;
      return '<button type="button" role="tab" class="tab' + (tab.id === state.category ? ' is-on' : '') +
        '" data-cat="' + esc(tab.id) + '" aria-selected="' + (tab.id === state.category) + '">' +
        esc(label) + '<span class="tab__count">' + tab.count + '</span></button>';
    }).join('');

    var sourceCounts = {};
    state.pool.forEach(function (a) {
      sourceCounts[a.source_id] = sourceCounts[a.source_id] || { name: a.source, n: 0 };
      sourceCounts[a.source_id].n++;
    });
    var sources = (state.data.sources || []).filter(function (s) { return sourceCounts[s.id]; });
    $('sourceFilter').innerHTML =
      '<option value="all">' + esc(t('allSources')) + '</option>' +
      sources.map(function (s) {
        return '<option value="' + esc(s.id) + '">' + esc(s.name) + ' (' + sourceCounts[s.id].n + ')</option>';
      }).join('');
    $('sourceFilter').value = state.source;

    var dates = state.index.slice();
    if (dates.indexOf(state.data.edition) < 0) dates.unshift(state.data.edition);
    $('archiveFilter').innerHTML =
      '<option value="latest">' + esc(t('latestEdition')) + '</option>' +
      dates.map(function (d) {
        return '<option value="' + esc(d) + '">' + esc(d) + '</option>';
      }).join('');
    $('archiveFilter').value = state.edition;
  }

  function renderChrome() {
    var data = state.data;
    var label = state.lang === 'zh' ? data.edition_label_zh : data.edition_label_en;
    $('editionLabel').textContent = label || data.edition;
    $('updatedLabel').textContent = t('updated') + ' ' + relativeTime(data.generated_at) +
      ' · ' + absoluteTime(data.generated_at);

    var stats = data.stats || {};
    var feeds = t('feedsOk', { ok: stats.feeds_ok || 0, total: stats.feeds_total || 0 });
    $('stats').innerHTML =
      '<div class="stat"><span class="stat__n">' + (stats.articles || 0) + '</span><span class="stat__l">' + esc(t('statStories')) + '</span></div>' +
      '<div class="stat"><span class="stat__n">' + ((data.sources || []).length) + '</span><span class="stat__l">' + esc(t('statSources')) + '</span></div>' +
      '<div class="stat"><span class="stat__n">' + (stats.build_seconds != null ? stats.build_seconds : '–') + '</span><span class="stat__l">' + esc(t('statBuild')) + '</span></div>';
    $('buildNote').textContent = feeds + ' · ' + (data.edition || '');
  }

  /* ------------------------------------------------------------- render -- */

  function matchesQuery(article, q) {
    if (!q) return true;
    return [article.title, article.title_zh, article.summary, article.summary_zh,
            article.source, catLabel(article)]
      .some(function (value) {
        return value && String(value).toLowerCase().indexOf(q) !== -1;
      });
  }

  function computePool() {
    var q = state.query.trim().toLowerCase();
    return state.pool.filter(function (a) {
      if (state.category !== 'all' && a.category !== state.category) return false;
      if (state.source !== 'all' && a.source_id !== state.source) return false;
      return matchesQuery(a, q);
    });
  }

  function pickHero(list) {
    var pristine = state.category === 'all' && state.source === 'all' && !state.query.trim();
    if (!pristine || state.pool.length < 12) return [];
    var newestPerCat = {};
    list.forEach(function (a) {
      if (!newestPerCat[a.category]) newestPerCat[a.category] = a;
    });
    return Object.keys(newestPerCat)
      .map(function (k) { return newestPerCat[k]; })
      .sort(function (a, b) { return String(b.published || '').localeCompare(String(a.published || '')); })
      .slice(0, 3);
  }

  function blankMarkup(article) {
    return '<span class="blank__inner">' +
             '<span class="blank__cat">' + esc(catLabel(article)) + '</span>' +
             '<span class="blank__rule"></span>' +
             '<span class="blank__src">' + esc(article.source || '') + '</span>' +
           '</span>';
  }

  function cardHtml(article, hero) {
    var url = safeUrl(article.url);
    var accent = catAccent(article);
    var rgb = hexToRgb(accent);
    var image = safeUrl(article.image);
    var summary = localized(article, 'summary');
    var title = localized(article, 'title');

    var media = '<div class="card__media' + (image ? '' : ' card__media--blank') + '"' +
      ' data-blank="' + esc(catLabel(article)) + '"' +
      ' data-src="' + esc(article.source || '') + '">' +
      (image
        ? '<img src="' + esc(image) + '" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">'
        : blankMarkup(article)) +
      '</div>';

    return '<a class="card' + (hero ? ' hero__card' : '') + '" href="' + esc(url) + '"' +
      ' target="_blank" rel="noopener noreferrer"' +
      ' style="--cat:' + esc(accent) + ';--cat-rgb:' + esc(rgb) + '"' +
      ' data-cat="' + esc(article.category) + '">' +
      media +
      '<div class="card__body">' +
        '<div class="card__top">' +
          '<span class="badge">' + esc(catLabel(article)) + '</span>' +
          '<span class="card__source">' + esc(article.source) + '</span>' +
          '<span class="card__time" title="' + esc(absoluteTime(article.published)) + '">' +
            esc(relativeTime(article.published)) + '</span>' +
        '</div>' +
        '<h3 class="card__title">' + esc(title) + '</h3>' +
        (summary ? '<p class="card__summary">' + esc(summary) + '</p>' : '') +
        '<div class="card__foot">' +
          '<span class="card__read">' + esc(t('read')) +
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 16 16 8M9.5 8H16v6.5" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
          '</span>' +
          (isTranslated(article, 'title') ? '<span class="card__lang">MT</span>' : '') +
        '</div>' +
      '</div>' +
    '</a>';
  }

  function fixBrokenImages(scope) {
    var imgs = scope.querySelectorAll('.card__media img');
    for (var i = 0; i < imgs.length; i++) {
      imgs[i].addEventListener('error', function () {
        var box = this.parentNode;
        if (!box || box.classList.contains('card__media--blank')) return;
        box.classList.add('card__media--blank');
        box.innerHTML =
          '<span class="blank__inner">' +
            '<span class="blank__cat">' + esc(box.getAttribute('data-blank') || '') + '</span>' +
            '<span class="blank__rule"></span>' +
            '<span class="blank__src">' + esc(box.getAttribute('data-src') || '') + '</span>' +
          '</span>';
      });
    }
  }

  // Auto-load the next page as the sentinel nears the viewport, with the button
  // kept as an explicit fallback (and for anyone with JS observers disabled).
  function refreshMoreObserver() {
    if (!moreObserver) return;
    moreObserver.disconnect();
    if (!$('moreWrap').hidden) moreObserver.observe($('moreWrap'));
  }

  function loadMore() {
    if (loadingMore) return;
    loadingMore = true;
    state.shown += PAGE_SIZE;
    render();
    loadingMore = false;
  }

  function setupInfiniteScroll() {
    if (!('IntersectionObserver' in window)) return;
    moreObserver = new IntersectionObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        if (entries[i].isIntersecting && !$('moreWrap').hidden) {
          loadMore();
          break;
        }
      }
    }, { rootMargin: '500px 0px' });
  }

  function render() {
    var list = computePool();
    var hero = pickHero(list);
    var heroIds = {};
    hero.forEach(function (a) { heroIds[a.id] = true; });
    var rest = list.filter(function (a) { return !heroIds[a.id]; });

    // Hero
    var heroBox = $('hero');
    if (hero.length) {
      $('heroGrid').innerHTML = hero.map(function (a) { return cardHtml(a, true); }).join('');
      heroBox.hidden = false;
      fixBrokenImages($('heroGrid'));
    } else {
      heroBox.hidden = true;
      $('heroGrid').innerHTML = '';
    }

    // Feed heading
    var titleKey = state.category === 'all' ? 'allNews' : null;
    $('feedTitle').textContent = titleKey
      ? t(titleKey)
      : (state.catMeta[state.category]
          ? (state.lang === 'zh' ? state.catMeta[state.category].zh : state.catMeta[state.category].en)
          : t('allNews'));
    $('feedCount').textContent = t('shownOf', { shown: Math.min(state.shown || PAGE_SIZE, rest.length), total: rest.length });

    // Grid
    state.shown = Math.min(Math.max(state.shown, PAGE_SIZE), Math.max(rest.length, 0));
    var slice = rest.slice(0, state.shown);
    $('grid').innerHTML = slice.map(function (a) { return cardHtml(a, false); }).join('');
    fixBrokenImages($('grid'));

    // Empty / more
    if (!list.length) {
      $('feedSection').hidden = true;
      $('moreWrap').hidden = true;
      var q = state.query.trim();
      showState('empty', t('noResults'),
        q ? t('noResultsText', { q: q, n: state.pool.length }) : t('noResultsTextPlain', { n: state.pool.length }),
        t('resetFilters'), resetFilters);
    } else {
      $('feedSection').hidden = false;
      hideState();
      var remaining = rest.length - slice.length;
      $('moreWrap').hidden = remaining <= 0;
      if (remaining > 0) {
        $('moreBtn').textContent = t('loadMore') + '  ·  ' + remaining;
      }
      refreshMoreObserver();
    }

    // Archive banner
    var banner = $('archiveNote');
    if (banner) banner.remove();
    if (state.edition !== 'latest') {
      var el = document.createElement('div');
      el.id = 'archiveNote';
      el.className = 'section-head';
      el.style.marginTop = '18px';
      el.innerHTML = '<span class="section-head__count" style="font-size:12.5px;color:var(--ink-2)">' +
        esc(t('archiveNote', { date: state.edition })) + ' </span>' +
        '<button type="button" class="tab is-on" id="backLatest" style="padding:4px 11px">' +
        esc(t('backToLatest')) + '</button>';
      $('main').insertBefore(el, $('hero'));
      $('backLatest').addEventListener('click', function () { switchEdition('latest'); });
    }
  }

  /* -------------------------------------------------------------- state -- */

  function showSkeleton(on) {
    $('skeleton').hidden = !on;
    if (on) {
      $('feedSection').hidden = true;
      $('moreWrap').hidden = true;
    }
  }

  function showState(kind, title, text, actionLabel, action) {
    var icons = {
      error: '<svg viewBox="0 0 24 24"><path d="M12 8.5v5.2M12 17h.01" stroke-linecap="round"/><path d="M10.3 3.9 2.6 17.4A1.9 1.9 0 0 0 4.3 20.3h15.4a1.9 1.9 0 0 0 1.7-2.9L13.7 3.9a1.9 1.9 0 0 0-3.4 0z"/></svg>',
      empty: '<svg viewBox="0 0 24 24"><circle cx="10.8" cy="10.8" r="6.5"/><path d="M15.6 15.6 20.5 20.5" stroke-linecap="round"/><path d="M8.4 10.8h4.8" stroke-linecap="round"/></svg>'
    };
    $('stateIcon').innerHTML = icons[kind] || '';
    $('stateTitle').textContent = title;
    $('stateText').textContent = text;
    var btn = $('stateAction');
    if (actionLabel && action) {
      btn.hidden = false;
      btn.textContent = actionLabel;
      btn.onclick = action;
    } else {
      btn.hidden = true;
      btn.onclick = null;
    }
    $('state').hidden = false;
  }

  function hideState() { $('state').hidden = true; }

  function resetFilters() {
    state.category = 'all';
    state.source = 'all';
    state.query = '';
    state.shown = PAGE_SIZE;
    $('search').value = '';
    $('searchClear').hidden = true;
    $('sourceFilter').value = 'all';
    write(LS.cat, 'all');
    buildControls();
    render();
  }

  function switchEdition(edition) {
    state.shown = PAGE_SIZE;
    load(edition).then(function () {
      $('archiveFilter').value = edition;
    });
  }

  function setCategory(cat) {
    state.category = cat;
    state.shown = PAGE_SIZE;
    write(LS.cat, cat);
    var tabs = document.querySelectorAll('.tab[data-cat]');
    for (var i = 0; i < tabs.length; i++) {
      var on = tabs[i].getAttribute('data-cat') === cat;
      tabs[i].classList.toggle('is-on', on);
      tabs[i].setAttribute('aria-selected', String(on));
    }
    render();
    var main = $('main');
    if (main.getBoundingClientRect().top < 0) {
      window.scrollTo({ top: main.offsetTop - 70, behavior: 'smooth' });
    }
  }

  /* -------------------------------------------------------------- wiring - */

  function wire() {
    $('tabs').addEventListener('click', function (event) {
      var tab = event.target.closest('.tab[data-cat]');
      if (tab) setCategory(tab.getAttribute('data-cat'));
    });

    var searchTimer = null;
    $('search').addEventListener('input', function () {
      var value = this.value;
      $('searchClear').hidden = !value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        state.query = value;
        state.shown = PAGE_SIZE;
        render();
      }, 160);
    });

    $('searchClear').addEventListener('click', function () {
      $('search').value = '';
      $('searchClear').hidden = true;
      state.query = '';
      state.shown = PAGE_SIZE;
      render();
      $('search').focus();
    });

    $('sourceFilter').addEventListener('change', function () {
      state.source = this.value;
      state.shown = PAGE_SIZE;
      render();
    });

    $('archiveFilter').addEventListener('change', function () {
      switchEdition(this.value);
    });

    $('moreBtn').addEventListener('click', loadMore);
    setupInfiniteScroll();

    $('themeToggle').addEventListener('click', function () {
      applyTheme(state.theme === 'dark' ? 'light' : 'dark');
    });

    document.querySelectorAll('.seg__btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        state.lang = btn.getAttribute('data-lang');
        write(LS.lang, state.lang);
        applyI18n();
        if (state.data) {
          state.shown = PAGE_SIZE;
          buildControls();
          renderChrome();
          render();
        }
      });
    });

    document.addEventListener('keydown', function (event) {
      if (event.key === '/' && document.activeElement !== $('search')) {
        event.preventDefault();
        $('search').focus();
      } else if (event.key === 'Escape' && document.activeElement === $('search')) {
        $('searchClear').click();
      }
    });
  }

  /* --------------------------------------------------------------- boot -- */

  function boot() {
    var preferred = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark' : 'light';
    applyTheme(state.theme || preferred);
    applyI18n();
    wire();

    loadIndex().then(function () {
      return load('latest');
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
