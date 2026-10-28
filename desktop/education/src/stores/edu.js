// stores/edu.js —— 教育科研工作台状态（Pinia options API，风格同 settings.js）
// 覆盖左侧 ①~⑦ 菜单、面板共享数据、③→④ 报告衔接、D3 投稿台账（localStorage）。
// 面板拟用 <component :is> 切换（切菜单卸载旧面板）：
//   - 进行中的请求由 store action 持有 → 面板卸载不中断；
//   - 表单草稿存 drafts → 切菜单不丢。
import { defineStore } from 'pinia';
import { pptBuildJob, pptCreateFromPdf, pptCreateJob, pptDeleteJob, pptSavePages, pptStyles, formatCheck, formatErrorMessage, getConferences, getDirectory, getJournals, presubmit, recommend, reviewErrorMessage, rebuttalConfirm, rebuttalCreateFromPdf, rebuttalCreateJob, rebuttalDeleteJob, rebuttalDraft, rebuttalLetter, rebuttalSaveResponse } from '../api/review.js';

const STORAGE_KEY = 'plantai.edu.submissions'; // D3 台账持久化（tracker 面板已解除映射，state 保留）
// tracker 已砍（2026-10-12，仅解除 UI 映射；TrackerPanel.vue 保留在 panels/ 下可复用）
const SECTIONS = ['journals', 'recommend', 'presubmit', 'report', 'format', 'calendar', 'rebuttal', 'ppt'];

// ⑥ 台账状态枚举（labels 由 step-7 UI 层给；store 只存字符串）
export const SUBMISSION_STATUS = ['draft', 'submitted', 'under_review', 'revision', 'accepted', 'rejected'];

function safeId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    try {
      return crypto.randomUUID();
    } catch {
      /* fallthrough */
    }
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function readStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((r) => r && typeof r === 'object' && typeof r.id === 'string');
  } catch {
    return [];
  }
}

export const useEduStore = defineStore('edu', {
  state: () => ({
    /* —— 菜单（①~⑦，默认①）—— */
    section: 'journals',

    /* —— ⑥ 投稿台账（D3；构造即加载 localStorage）—— */
    submissions: readStorage(), // {id, journal, status, submittedAt, updatedAt, notes}

    /* —— 面板数据三态（统一 idle | loading | ready | error）—— */
    journals: [], // ① items
    journalsMeta: null, // {count, last_updated, data_source_note}
    journalsStatus: 'idle',
    journalsError: null,
    /* —— ①' 期刊目录（journal_directory 1056 本；与 journals 5 本精选三态隔离）—— */
    journalsTab: 'en', // 'en' | 'zh' 视图态（切 Tab 全清筛选，见 resetDirectoryQuery）
    directory: [], // 当前页 items（后端已过滤/排序/分页）
    directoryMeta: null, // {total,page,size,lang,domain,cas_partition,q,domains[],data_source_note,data_imported}
    directoryStatus: 'idle', // idle | loading | ready | error
    directoryError: null,
    // 请求参数与 directory 同生命周期 → 切菜单（面板卸载）不丢筛选
    directoryQuery: {
      lang: 'en', q: '', domain: '', cas_partition: '',
      page: 1, size: 50, field: '', order: '',
    },
    // 竞态令牌：防抖/连点/切 Tab 时旧响应覆盖新数据（fetchDirectory 内比对）
    directorySeq: 0,
    conferences: [], // ⑦' 会议时间线 items（后端已过滤/排序；首版全 placeholder）
    conferencesStatus: 'idle',
    conferencesError: null,
    report: null, // ③→④ presubmit 响应 {ok, report, meta}
    reportCreatedAt: null, // ④报告头生成时间（ISO；payload 无时间字段，提交成功时盖章，clearReport 重置）
    reportStatus: 'idle',
    reportError: null,
    // ③ presubmit 已选 PDF（File 对象，仅内存：面板卸载/切菜单不丢，error 态可直接重试；
    //    不可序列化 → 不进 localStorage；clearReport 一并清空）
    presubmitFile: null,
    recommend: null, // ② 推荐响应 {ok, tiers, meta}
    recommendStatus: 'idle',
    recommendError: null,
    formatReport: null, // ⑤ 版式检查响应 {ok, checks, summary, meta}（跨菜单保留）
    formatStatus: 'idle',
    formatError: null,
    /* —— 表单草稿（切菜单不丢；四字段并存：recommend 用 abstract+prefs、
       presubmit 用 targetJournal、format 用 formatJournalId（期刊 id 下拉））—— */
    drafts: {
      abstract: '',
      targetJournal: '',
      formatJournalId: '',
      // 与 POST /api/review/recommend 的 preferences 同构：
      // {max_review_weeks, min_impact_factor, tier_focus} 子字段逐一同名同型，
      // 提交时整体映射为 payload.preferences（null 数值剔除）。
      // 首版 chips 为纯前端筛选 → tier_focus 恒为默认 'all'，不改 payload 语义。
      prefs: { max_review_weeks: null, min_impact_factor: null, tier_focus: 'all' },
      // 结果区档位筛选（纯视图状态，不参与 payload；随 store 保留跨菜单）
      tierFilter: 'all',
    },

    /* —— Rebuttal Phase 1（审稿回复助手；纯内存态，切换面板不丢，刷新丢失）—— */
    rebuttal: {
      job: null, // 后端全量 job（reviewers/letter_md/human_ack...）
      status: 'idle', // idle | loading | ready | error
      error: null,
      expired: false, // NOT_FOUND（job 已删除/过期）→ 面板显示"任务已过期"
      activeCid: null, // 当前编辑的 comment id
      letterLoading: false,
      draftCid: null, // 正在生成 draft 的 comment（按钮 loading）
      // Tracker「去写返修」带来的期刊名预填（方案 A：仅跳转，无 jobId 回写）
      prefillJournal: '',
    },

    /* —— P5 风格清单（GET /ppt/styles；与 job 生命周期无关，clearPpt/删除都不清）—— */
    pptStyles: [], // [{id,label,display_name,description,bg,accent}]
    pptStylesStatus: 'idle', // idle | loading | ready | error（error 允许下次面板挂载重试）

    /* —— P5 科研汇报助手（PPT 大纲 job；纯内存态，切换面板不丢，刷新丢失）—— */
    ppt: {
      job: null, // 后端全量 job（pages/warnings/status...；raw_text 不回传）
      status: 'idle', // idle | loading | editing | building | done | error
      error: null,
      saving: false, // PATCH 进行中（编辑器保存按钮 loading）
      buildMeta: null, // 最近一次 {slides, bytes}（编辑后清空，提示重新生成）
      createMeta: null, // 生成期 meta（llm_calls/target_pages... 供页头展示）
      // 生成表单（跨菜单保留；style/scenario/minutes 在创建时固化进 job）
      form: {
        mode: 'pdf', // 'pdf' | 'text'
        scenario: 'group_meeting',
        minutes: 15,
        styleId: 'academic-minimal',
        file: null, // File（pdf 模式；每次生成后保留，便于重试）
        text: '', // 文本模式草稿
      },
    },
  }),

  getters: {
    hasReport: (s) => !!(s.report && s.report.ok && s.report.report),
    submissionsSorted: (s) =>
      [...s.submissions].sort((a, b) => String(b.updatedAt || '').localeCompare(a.updatedAt || '')),
  },

  actions: {
    setSection(k) {
      if (SECTIONS.includes(k)) this.section = k;
    },

    /* —— D3 台账 CRUD（每步 _persist 同步 localStorage）—— */
    loadFromStorage() {
      this.submissions = readStorage();
    },
    addSubmission({ journal, status = 'draft', notes = '', submittedAt = null, revisionDue = '', revisionRound = null, rebuttalJobId = null }) {
      const now = new Date().toISOString();
      const rec = {
        id: safeId(), journal, status,
        submittedAt: submittedAt || now,
        updatedAt: now, notes,
        revisionDue: revisionDue || '',      // 返修截止日 'YYYY-MM-DD'（可空）
        revisionRound: revisionRound || null, // 返修轮次（可空）
        rebuttalJobId: rebuttalJobId || null, // 关联的 rebuttal job（开始返修回写）
      };
      this.submissions.unshift(rec);
      this._persist();
      return rec;
    },
    updateSubmission(id, patch = {}) {
      const i = this.submissions.findIndex((s) => s.id === id);
      if (i < 0) return null;
      const merged = { ...this.submissions[i], ...patch, id, updatedAt: new Date().toISOString() };
      this.submissions[i] = merged;
      this._persist();
      return merged;
    },
    removeSubmission(id) {
      this.submissions = this.submissions.filter((s) => s.id !== id);
      this._persist();
    },
    _persist() {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(this.submissions));
      } catch {
        /* 隐私模式/配额满：内存态可用，静默降级 */
      }
    },

    /* —— API actions（错误吞入 *Status/*Error 状态，不 rethrow；成功返回 payload，失败 null）—— */
    async fetchJournals(params = {}) {
      this.journalsStatus = 'loading';
      this.journalsError = null;
      try {
        const payload = await getJournals(params);
        this.journals = (payload && payload.items) || [];
        this.journalsMeta = (payload && payload.meta) || null;
        this.journalsStatus = 'ready';
        return payload;
      } catch (err) {
        this.journalsStatus = 'error';
        this.journalsError = reviewErrorMessage(err);
        return null;
      }
    },
    /* —— ①' 目录查询（请求参数合并 → 只读 GET；seq 防乱序覆盖）—— */
    async fetchDirectory(partial = {}) {
      Object.assign(this.directoryQuery, partial);
      const seq = ++this.directorySeq;
      this.directoryStatus = 'loading';
      this.directoryError = null;
      try {
        const payload = await getDirectory({ ...this.directoryQuery });
        if (seq !== this.directorySeq) return null; // 过期响应：丢弃
        this.directory = (payload && payload.items) || [];
        this.directoryMeta = (payload && payload.meta) || null;
        this.directoryStatus = 'ready';
        return payload;
      } catch (err) {
        if (seq !== this.directorySeq) return null;
        this.directoryStatus = 'error';
        this.directoryError = reviewErrorMessage(err);
        return null;
      }
    },
    /* 复位筛选（保留 size）：切 Tab / 清除筛选共用 */
    resetDirectoryQuery(newLang = this.directoryQuery.lang) {
      this.directoryQuery = {
        lang: newLang,
        q: '', domain: '', cas_partition: '',
        page: 1,
        size: this.directoryQuery.size,
        field: '', order: '',
      };
    },
    /* 切 Tab：全清 q/domain/cas_partition/page（en/zh 筛选语义不对称，保留会困惑） */
    async switchDirectoryTab(tab) {
      if (tab !== 'en' && tab !== 'zh' || tab === this.journalsTab) return null;
      this.journalsTab = tab;
      this.resetDirectoryQuery(tab);
      return this.fetchDirectory();
    },

    async fetchConferences(query = {}) {
      this.conferencesStatus = 'loading';
      this.conferencesError = null;
      try {
        const payload = await getConferences(query);
        this.conferences = (payload && payload.items) || [];
        this.conferencesStatus = 'ready';
        return payload;
      } catch (err) {
        this.conferencesStatus = 'error';
        this.conferencesError = reviewErrorMessage(err);
        return null;
      }
    },
    async submitPresubmit(formData, opts = {}) {
      this.reportStatus = 'loading';
      this.reportError = null;
      try {
        const payload = await presubmit(formData, opts); // opts 透传 onUploadProgress（http.js XHR 分支）
        this.report = payload;
        this.reportCreatedAt = new Date().toISOString();
        this.reportStatus = 'ready';
        return payload;
      } catch (err) {
        this.reportStatus = 'error';
        this.reportError = reviewErrorMessage(err);
        return null;
      }
    },
    clearReport() {
      this.report = null;
      this.reportCreatedAt = null;
      this.reportStatus = 'idle';
      this.reportError = null;
      this.presubmitFile = null;
    },

    /* —— Rebuttal Phase 1 actions（job 为后端权威，本地只镜像）—— */
    _rebuttalBase() {
      return {
        job: null, status: 'idle', error: null, expired: false, activeCid: null,
        letterLoading: false, draftCid: null, prefillJournal: '',
      };
    },
    _rebuttalAfterCreate(res) {
      this.rebuttal.job = res.job;
      this.rebuttal.status = 'ready';
      return res.job;
    },
    async rebuttalCreate(text) {
      this.rebuttal = { ...this._rebuttalBase(), status: 'loading', prefillJournal: this.rebuttal.prefillJournal };
      try {
        const res = await rebuttalCreateJob(text);
        return this._rebuttalAfterCreate(res);
      } catch (err) {
        this.rebuttal.status = 'error';
        this.rebuttal.error = reviewErrorMessage(err);
        return null;
      }
    },
    async rebuttalCreatePdf(formData) {
      this.rebuttal = { ...this._rebuttalBase(), status: 'loading', prefillJournal: this.rebuttal.prefillJournal };
      try {
        const res = await rebuttalCreateFromPdf(formData);
        return this._rebuttalAfterCreate(res);
      } catch (err) {
        this.rebuttal.status = 'error';
        this.rebuttal.error = reviewErrorMessage(err);
        return null;
      }
    },
    /** Tracker「去写返修」（方案 A）：仅跳转 + 期刊名预填，不做 jobId 回写联动 */
    rebuttalStartFromTracker(row) {
      this.clearRebuttal();
      this.setSection('rebuttal');
      this.rebuttal.prefillJournal = row?.journal || '';
    },
    rebuttalSetActive(cid) {
      this.rebuttal.activeCid = cid;
    },
    _applyRebuttalJob(job) {
      if (job) this.rebuttal.job = job;
    },
    async rebuttalSaveResponse(cid, parts) {
      try {
        const res = await rebuttalSaveResponse(this.rebuttal.job.id, cid, parts);
        this._applyRebuttalJob(res.job);
        return { ok: true };
      } catch (err) {
        return { ok: false, error: reviewErrorMessage(err), code: err?.code };
      }
    },
    async rebuttalDraft(cid) {
      this.rebuttal.draftCid = cid;
      try {
        const res = await rebuttalDraft(this.rebuttal.job.id, cid);
        this._applyRebuttalJob(res.job);
        return { ok: true, validation: res.meta?.validation };
      } catch (err) {
        // VALIDATION_FAILED：信封里的 job 已带 validation，一并镜像
        if (err?.payload?.job) this._applyRebuttalJob(err.payload.job);
        return {
          ok: false,
          error: reviewErrorMessage(err),
          code: err?.code,
          validation: err?.payload?.meta?.validation || null,
        };
      } finally {
        // 成功/失败/异常都要解锁按钮——否则校验报红后按钮永卡"生成中"
        this.rebuttal.draftCid = null;
      }
    },
    // Phase 1 砍手工合并/拆分（2026-10-12）：PATCH /structure 后端端点保留，前端不调
    async rebuttalConfirm(cid, confirmed) {
      try {
        const res = await rebuttalConfirm(this.rebuttal.job.id, cid, confirmed);
        this._applyRebuttalJob(res.job);
        return { ok: true, humanAck: res.meta?.human_ack ?? res.job?.human_ack };
      } catch (err) {
        return { ok: false, error: reviewErrorMessage(err), code: err?.code };
      }
    },
    async rebuttalAssembleLetter() {
      try {
        const res = await rebuttalLetter(this.rebuttal.job.id);
        this._applyRebuttalJob(res.job);
        return { ok: true };
      } catch (err) {
        return { ok: false, error: reviewErrorMessage(err), code: err?.code };
      }
    },
    async rebuttalDelete() {
      const id = this.rebuttal.job?.id;
      if (!id) return { ok: true };
      try {
        await rebuttalDeleteJob(id);
        this.clearRebuttal();
        return { ok: true };
      } catch (err) {
        return { ok: false, error: reviewErrorMessage(err) };
      }
    },
    clearRebuttal() {
      this.rebuttal = this._rebuttalBase();
    },

    /* —— P5 科研汇报助手 actions（job 为后端权威，本地只镜像）—— */
    /** 风格清单（面板挂载时取一次；失败留空 → 副标题回落「多风格」文案） */
    async pptLoadStyles() {
      if (this.pptStylesStatus === 'loading' || this.pptStylesStatus === 'ready') {
        return this.pptStyles;
      }
      this.pptStylesStatus = 'loading';
      try {
        const res = await pptStyles();
        const items = Array.isArray(res && res.items) ? res.items : [];
        this.pptStyles = items;
        this.pptStylesStatus = 'ready';
      } catch {
        this.pptStyles = [];
        this.pptStylesStatus = 'error';
      }
      return this.pptStyles;
    },

    _pptBase() {
      return {
        job: null,
        status: 'idle',
        error: null,
        saving: false,
        buildMeta: null,
        createMeta: null,
        form: { ...this.ppt.form },
      };
    },
    /** 生成大纲（pdf 上传 / 文本粘贴，按 form.mode 分派；同步长调用） */
    async pptGenerate() {
      const f = this.ppt.form;
      if (f.mode === 'pdf' && !f.file) {
        this.ppt.status = 'error';
        this.ppt.error = '请先选择 PDF 文件';
        return null;
      }
      if (f.mode === 'text' && (!f.text || f.text.trim().length < 300)) {
        this.ppt.status = 'error';
        this.ppt.error = '正文过短（<300 字），无法生成汇报大纲';
        return null;
      }
      this.ppt.status = 'loading';
      this.ppt.error = null;
      try {
        let res;
        if (f.mode === 'pdf') {
          const fd = new FormData();
          fd.append('file', f.file);
          fd.append('scenario', f.scenario);
          fd.append('minutes', String(f.minutes));
          fd.append('style_id', f.styleId);
          res = await pptCreateFromPdf(fd);
        } else {
          res = await pptCreateJob({
            text: f.text, scenario: f.scenario, minutes: f.minutes, style_id: f.styleId,
          });
        }
        this.ppt.job = res.job;
        this.ppt.createMeta = res.meta || null;
        this.ppt.buildMeta = null;
        this.ppt.status = 'editing';
        return res.job;
      } catch (err) {
        this.ppt.status = 'error';
        this.ppt.error = reviewErrorMessage(err);
        return null;
      }
    },
    /** 保存编辑后的完整 pages（后端结构清洗 + evidence 重验；失败保留编辑态） */
    async pptSave(pages) {
      if (!this.ppt.job) return null;
      this.ppt.saving = true;
      this.ppt.error = null;
      try {
        const res = await pptSavePages(this.ppt.job.id, pages);
        this.ppt.job = res.job;
        this.ppt.buildMeta = null; // 旧 pptx 失效（后端亦清 pptx_path）
        return res.job;
      } catch (err) {
        this.ppt.error = reviewErrorMessage(err);
        return null;
      } finally {
        this.ppt.saving = false;
      }
    },
    /** 大纲 → draft.pptx（python-pptx 本地生成） */
    async pptBuild() {
      if (!this.ppt.job) return null;
      this.ppt.status = 'building';
      this.ppt.error = null;
      try {
        const res = await pptBuildJob(this.ppt.job.id);
        this.ppt.job = res.job;
        this.ppt.buildMeta = res.meta || null;
        this.ppt.status = 'done';
        return res.job;
      } catch (err) {
        this.ppt.status = 'error';
        this.ppt.error = reviewErrorMessage(err);
        return null;
      }
    },
    /** 删除 job（行 + 产物目录）；保留表单便于立即重做 */
    async pptDelete() {
      const id = this.ppt.job?.id;
      if (!id) return { ok: true };
      try {
        await pptDeleteJob(id);
        this.ppt = this._pptBase();
        return { ok: true };
      } catch (err) {
        this.ppt.error = reviewErrorMessage(err);
        return { ok: false, error: this.ppt.error };
      }
    },
    /** 回到生成表单（保留表单值，清 job/产物状态） */
    clearPpt() {
      this.ppt = this._pptBase();
    },
    async submitRecommend(payload) {
      this.recommendStatus = 'loading';
      this.recommendError = null;
      try {
        const res = await recommend(payload);
        this.recommend = res;
        this.recommendStatus = 'ready';
        return res;
      } catch (err) {
        this.recommendStatus = 'error';
        this.recommendError = reviewErrorMessage(err);
        return null;
      }
    },
    async submitFormatCheck(formData) {
      this.formatStatus = 'loading';
      this.formatError = null;
      try {
        const payload = await formatCheck(formData); // 纯规则引擎，同步秒回
        this.formatReport = payload;
        this.formatStatus = 'ready';
        return payload;
      } catch (err) {
        this.formatStatus = 'error';
        this.formatError = formatErrorMessage(err);
        return null;
      }
    },
    clearFormatReport() {
      this.formatReport = null;
      this.formatStatus = 'idle';
      this.formatError = null;
    },
  },
});
