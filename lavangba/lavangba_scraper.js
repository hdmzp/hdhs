(function () {
  // ================= 설정 =================
  // 기본값: 어제(KST) 하루. 필요하면 아래 TARGET_DATES를 직접 채워서 사용 ('YYYYMMDD' 문자열 배열)
  function kstNow() {
    return new Date(Date.now() + 9 * 3600 * 1000); // UTC getter들을 KST 달력값처럼 사용하기 위한 트릭
  }
  function toYmd(d) {
    return d.getUTCFullYear() + String(d.getUTCMonth() + 1).padStart(2, '0') + String(d.getUTCDate()).padStart(2, '0');
  }
  function dateRange(startYmd, endYmd) {
    const out = [];
    let cur = new Date(Date.UTC(+startYmd.slice(0, 4), +startYmd.slice(4, 6) - 1, +startYmd.slice(6, 8)));
    const end = new Date(Date.UTC(+endYmd.slice(0, 4), +endYmd.slice(4, 6) - 1, +endYmd.slice(6, 8)));
    while (cur <= end) {
      out.push(toYmd(cur));
      cur = new Date(cur.getTime() + 86400000);
    }
    return out;
  }

  const yesterday = toYmd(new Date(kstNow().getTime() - 86400000));
  const TARGET_DATES = [yesterday];
  // 특정 날짜만: const TARGET_DATES = ['20260709'];
  // 기간: const TARGET_DATES = dateRange('20260701', '20260709');

  // ================= 채널 매핑 =================
  // key: 라방바 platform_id, value: GitHub(hdmzp/hdhs) homeshopping 폴더 코드
  const GITHUB_CODE = {
    hs_gsshop: 'GS',
    hs_cjonstyle: 'CJ',
    hs_hmall: 'HD',
    hs_lotteimall: 'LT',
    hs_nsmall: 'NS',
    hs_gongyoung: 'PUBLIC',
    hs_shinsegae: 'SHINSEGAE',
    hs_shopntmall: 'SHOPPINGNT',
    hs_skstoa: 'SKSTOA',
    hs_hnsmall: 'HNS',
    hs_kshop: 'KTALPHA',
  };
  // 제외된 것: hs_hmallplus / hs_gsshopmyshop / hs_lotteimallonetv / hs_nsmallshopplus / hs_cjonstyleplus
  // (TV 11개사 외의 데이터홈쇼핑/플러스 채널 — 필요하면 GITHUB_CODE에 추가)

  // ================= API =================
  async function fetchListHs(dateStr) {
    const yymmdd = dateStr.slice(2);
    const res = await fetch('https://live.ecomm-data.com/api/schedule/list_hs', {
      method: 'POST',
      headers: { 'content-type': 'application/json', domain: 'ecomm-data.com' },
      body: JSON.stringify({ date: yymmdd }),
      credentials: 'omit',
    });
    const data = await res.json();
    return (data.list || []).filter((x) => GITHUB_CODE[x.platform_id]);
  }

  async function fetchItemsAll(hshowId, expectedCount) {
    let page = 1;
    const size = 50;
    let allItems = [];
    while (true) {
      const res = await fetch('https://live.ecomm-data.com/api/hsshow/items', {
        method: 'POST',
        headers: { 'content-type': 'application/json', domain: 'ecomm-data.com' },
        body: JSON.stringify({ hsshow_id: hshowId, page, size, order: ['sales_amt/desc'], with_rcd: true }),
        credentials: 'include',
      });
      const data = await res.json();
      const items = data.items || [];
      allItems = allItems.concat(items);
      if (items.length < size || allItems.length >= (data.total_count || expectedCount || 0)) break;
      page++;
    }
    return allItems;
  }

  const githubCache = {}; // 'CODE|YYYY-MM' -> parsed json | null
  async function fetchGithubMonth(code, yyyyMm) {
    const key = code + '|' + yyyyMm;
    if (key in githubCache) return githubCache[key];
    const url = 'https://raw.githubusercontent.com/hdmzp/hdhs/main/homeshopping/' + code + '_live/' + yyyyMm + '.json';
    let data = null;
    try {
      const res = await fetch(url);
      if (res.ok) data = await res.json();
    } catch (e) {
      /* 네트워크 오류 시 null 유지 */
    }
    githubCache[key] = data;
    return data;
  }

  // ================= 시간 유틸 =================
  function hsToDate(s) {
    // 'YYYYMMDDHHMM'
    const y = s.slice(0, 4), mo = s.slice(4, 6), d = s.slice(6, 8), h = s.slice(8, 10), mi = s.slice(10, 12);
    return new Date(y + '-' + mo + '-' + d + 'T' + h + ':' + mi + ':00+09:00');
  }
  function ymdToHyphen(dateStr) {
    return dateStr.slice(0, 4) + '-' + dateStr.slice(4, 6) + '-' + dateStr.slice(6, 8);
  }
  function ghEntryDates(dateHyphen, entry) {
    const start = new Date(dateHyphen + 'T' + entry.start + ':00+09:00');
    let end = new Date(dateHyphen + 'T' + entry.end + ':00+09:00');
    if (end <= start) end = new Date(end.getTime() + 86400000); // 자정 넘어가는 세그먼트 보정
    return { start, end };
  }
  function formatAmt(total) {
    return total >= 100000000 ? (total / 100000000).toFixed(2) + '억' : Math.round(total / 10000) + '만';
  }

  // ================= 세그먼트 분류 (복합PGM) =================
  function classifyItemToSegment(item, segments) {
    const rcd = (item.sales_amt_rcd || '').split(',').map(Number);
    let bestIdx = null, bestSum = -1;
    segments.forEach((seg) => {
      let sum = 0;
      for (let i = seg.from; i < seg.to && i < rcd.length; i++) sum += rcd[i] || 0;
      if (sum > bestSum) { bestSum = sum; bestIdx = seg.idx; }
    });
    return { idx: bestIdx, activity: bestSum };
  }

  // ================= 메인 =================
  async function run() {
    const rows = [];
    let hshowCount = 0;

    for (const dateStr of TARGET_DATES) {
      console.log('[list_hs] ' + dateStr + ' 조회 중...');
      const list = await fetchListHs(dateStr);
      console.log('  -> 대상 11개사 방송 ' + list.length + '건');

      const dateHyphen = ymdToHyphen(dateStr);
      const yyyyMm = dateStr.slice(0, 4) + '-' + dateStr.slice(4, 6);

      for (const hshow of list) {
        hshowCount++;
        const code = GITHUB_CODE[hshow.platform_id];
        const channelLabel = hshow.platform_name || code;

        const monthData = await fetchGithubMonth(code, yyyyMm);
        const dayEntries = (monthData && monthData.days && monthData.days[dateHyphen]) || [];

        const hshowStart = hsToDate(hshow.hsshow_datetime_start);
        const hshowEnd = hsToDate(hshow.hsshow_datetime_end);
        const pgmStartLabel =
          String(hshowStart.getHours()).padStart(2, '0') + ':' + String(hshowStart.getMinutes()).padStart(2, '0');

        // 해당 hshow 방영 구간과 겹치는 GitHub 편성 항목 찾기
        const matched = dayEntries
          .map((e) => ({ entry: e, ...ghEntryDates(dateHyphen, e) }))
          .filter((e) => e.start < hshowEnd && e.end > hshowStart)
          .sort((a, b) => a.start - b.start);

        console.log(
          '[' + channelLabel + ' ' + dateStr + ' ' + pgmStartLabel + '] item_cnt=' + hshow.item_cnt + ' fetching items...'
        );

        let items = [];
        try {
          items = await fetchItemsAll(hshow.hsshow_id, hshow.item_cnt);
        } catch (e) {
          console.warn('[오류] items 조회 실패:', hshow.hsshow_title, e.message);
        }

        const isComplex = items.length > 1 && matched.length >= 2;

        if (!isComplex) {
          // 단순: 전체 아이템 매출 합산, 상품명은 라방바 hsshow_title 기준
          const total = items.reduce((s, p) => s + (p.sales_amt || 0), 0);
          const bestGh = matched[0]; // best-effort 카테고리 참고용 (여러개면 첫 매칭)
          rows.push(
            [
              channelLabel,
              '단순',
              pgmStartLabel,
              hshow.hsshow_title,
              hshow.hsshow_title,
              total,
              bestGh ? bestGh.entry.category : '',
              bestGh ? bestGh.entry.lavangba_category || '' : '',
              hshow.cat && hshow.cat.cat_name ? hshow.cat.cat_name : '',
            ].join('\t')
          );
          console.log('  단순 | ' + formatAmt(total));
        } else if (items.length > 1 && matched.length < 2) {
          // 복합이지만 GitHub 세그먼트를 못 찾음 -> 라방바 개별 상품 그대로 나열
          console.warn('[경고] 편성 세그먼트 매칭 실패 (개별 SKU로 나열):', channelLabel, dateStr, pgmStartLabel, hshow.hsshow_title);
          items.forEach((item) => {
            rows.push(
              [
                channelLabel,
                '복합(미분류)',
                pgmStartLabel,
                hshow.hsshow_title,
                item.item_name,
                item.sales_amt || 0,
                '',
                '',
                hshow.cat && hshow.cat.cat_name ? hshow.cat.cat_name : '',
              ].join('\t')
            );
          });
        } else {
          // 복합: GitHub 세그먼트 시간대 기준으로 라방바 아이템들의 시계열을 분류/집계
          const durationMin = Math.round((hshowEnd - hshowStart) / 60000);
          const segments = matched.map((m, i) => ({
            idx: i,
            from: Math.max(0, Math.round((m.start - hshowStart) / 60000)),
            to: Math.min(durationMin, Math.round((m.end - hshowStart) / 60000)),
            entry: m.entry,
          }));

          const segTotals = {};
          items.forEach((item) => {
            const { idx, activity } = classifyItemToSegment(item, segments);
            if (idx === null) return;
            if (activity <= 0) {
              console.warn('[경고] 시계열 활동 없는 상품(0원 판단 가능):', item.item_name);
            }
            segTotals[idx] = (segTotals[idx] || 0) + (item.sales_amt || 0);
          });

          segments.forEach((seg) => {
            const total = segTotals[seg.idx] || 0;
            rows.push(
              [
                channelLabel,
                '복합',
                pgmStartLabel,
                hshow.hsshow_title,
                seg.entry.product,
                total,
                seg.entry.category,
                seg.entry.lavangba_category || '',
                hshow.cat && hshow.cat.cat_name ? hshow.cat.cat_name : '',
              ].join('\t')
            );
            console.log('  복합(시점분류) ' + seg.entry.product.substring(0, 20) + ' | ' + formatAmt(total));
          });
        }

        await new Promise((r) => setTimeout(r, 250));
      }
    }

    console.log('완료! 총 ' + rows.length + '행 (' + hshowCount + '개 방송)');

    // ================= 결과 모달 =================
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:99998;';
    const modal = document.createElement('div');
    modal.style.cssText =
      'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:820px;background:white;border-radius:12px;padding:20px;z-index:99999;box-shadow:0 20px 60px rgba(0,0,0,0.3);';
    const titleEl = document.createElement('div');
    titleEl.style.cssText = 'font-weight:bold;font-size:14px;margin-bottom:12px;color:#333;';
    titleEl.textContent =
      '매출 결과 (' + rows.length + '행, ' + hshowCount + '개 방송) - 컬럼: 채널/유형/방송시작/방송제목/상품명/매출액/출처카테고리/라방바세부카테고리/라방바대분류';
    const ta = document.createElement('textarea');
    ta.value = rows.join('\n');
    ta.style.cssText =
      'width:100%;height:360px;font-size:11px;font-family:monospace;border:1px solid #ddd;border-radius:6px;padding:8px;box-sizing:border-box;resize:none;';
    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;margin-top:12px;';
    const copyBtn = document.createElement('button');
    copyBtn.textContent = '전체 복사';
    copyBtn.style.cssText = 'flex:1;padding:10px;background:#4a9eff;color:white;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:bold;';
    copyBtn.onclick = () => {
      ta.select();
      document.execCommand('copy');
      copyBtn.textContent = '복사됨!';
      setTimeout(() => (copyBtn.textContent = '전체 복사'), 2000);
    };
    const downloadBtn = document.createElement('button');
    downloadBtn.textContent = '엑셀 다운로드';
    downloadBtn.style.cssText = 'flex:1;padding:10px;background:#2ecc71;color:white;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:bold;';
    downloadBtn.onclick = () => {
      const dataRows = ta.value.split('\n').map((row) => row.split('\t'));
      let htmlTable = '<table border="1">';
      dataRows.forEach((row) => {
        htmlTable += '<tr>' + row.map((cell) => '<td>' + cell + '</td>').join('') + '</tr>';
      });
      htmlTable += '</table>';
      const blob = new Blob(['﻿' + htmlTable], { type: 'application/vnd.ms-excel;charset=utf-8;' });
      const link = document.createElement('a');
      const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
      link.href = URL.createObjectURL(blob);
      link.download = '홈쇼핑11사_매출결과_' + today + '.xls';
      link.click();
    };
    const closeBtn = document.createElement('button');
    closeBtn.textContent = '닫기';
    closeBtn.style.cssText = 'padding:10px 20px;background:#999;color:white;border:none;border-radius:6px;cursor:pointer;font-size:13px;';
    closeBtn.onclick = () => {
      document.body.removeChild(overlay);
      document.body.removeChild(modal);
    };
    btnRow.appendChild(copyBtn);
    btnRow.appendChild(downloadBtn);
    btnRow.appendChild(closeBtn);
    modal.appendChild(titleEl);
    modal.appendChild(ta);
    modal.appendChild(btnRow);
    document.body.appendChild(overlay);
    document.body.appendChild(modal);
    ta.select();
  }

  run();
})();
