const state = {
  catalog: null,
  lastResult: null,
  lastManual: null,
  lastSelection: null,
  llmProposal: null,
  sessionDir: null,
};

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

function toast(message, error=false) {
  const node = $('toast');
  node.textContent = message;
  node.classList.toggle('error', error);
  node.classList.remove('hidden');
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.add('hidden'), 4300);
}

function switchMode(mode) {
  document.querySelectorAll('.mode-button').forEach(button => button.classList.toggle('active', button.dataset.mode === mode));
  document.querySelectorAll('.mode-panel').forEach(panel => panel.classList.toggle('active', panel.id === `mode-${mode}`));
}

function activeSelection() {
  const id = $('module-select').value;
  return state.catalog.selections.find(item => item.selection_id === id);
}

function renderManualFields() {
  const selection = activeSelection();
  state.lastSelection = selection;
  const root = $('manual-fields');
  root.innerHTML = '';
  (selection?.fields || []).forEach(field => {
    const label = document.createElement('label');
    label.className = `field ${field.required ? 'required' : ''}`;
    label.dataset.field = field.name;
    const caption = document.createElement('span');
    caption.innerHTML = `${esc(field.label)}${field.unit ? ` <b>${esc(field.unit)}</b>` : ''}`;
    let input;
    if (field.type === 'select') {
      input = document.createElement('select');
      (field.options || []).forEach(option => {
        const node = document.createElement('option');
        node.value = option;
        node.textContent = option || '—';
        input.appendChild(node);
      });
    } else {
      input = document.createElement('input');
      input.type = field.type || 'text';
      if (field.type === 'number') input.step = field.integer ? '1' : 'any';
      input.placeholder = field.placeholder || '';
    }
    input.id = `field-${field.name}`;
    label.append(caption, input);
    root.appendChild(label);
  });
}

function collectManualValues() {
  const values = {};
  document.querySelectorAll('#manual-fields [data-field]').forEach(label => {
    const name = label.dataset.field;
    const input = label.querySelector('input,select,textarea');
    if (!input || input.value.trim() === '') return;
    values[name] = input.type === 'number' ? Number(input.value) : input.value.trim();
  });
  return values;
}

function fillManualValues(values) {
  Object.entries(values || {}).forEach(([name, value]) => {
    const input = $(`field-${name}`);
    if (input) input.value = value ?? '';
  });
}

function resultObject(value) {
  if (!value) return null;
  if (value.result) return value.result;
  if (value.value?.result) return value.value.result;
  if (value.equipment && Array.isArray(value.equipment)) return value.equipment[0]?.match_result || value.equipment[0];
  if (value.result?.equipment) return value.result.equipment[0]?.match_result || value.result.equipment[0];
  return value;
}

function findEquipmentRecords(value) {
  if (Array.isArray(value?.equipment)) return value.equipment;
  if (Array.isArray(value?.result?.equipment)) return value.result.equipment;
  return [];
}

function renderResult(payload) {
  state.lastResult = payload;
  $('save-result').disabled = !payload;
  $('empty-result').classList.toggle('hidden', !!payload);
  $('result-summary').classList.toggle('hidden', !payload);
  $('equation-list').classList.toggle('hidden', !payload);
  $('raw-details').classList.toggle('hidden', !payload);
  if (!payload) return;

  let result = resultObject(payload) || {};
  const equipmentRecords = findEquipmentRecords(payload);
  if (equipmentRecords.length) result = equipmentRecords[0].match_result || equipmentRecords[0];
  const match = result.match || {};
  const model = result.model_decision || {};
  const status = result.status || payload.status || '—';
  const family = match.family_name || match.family_id || equipmentRecords[0]?.aspen_block_id || '—';
  const modelStatus = model.model_status || '—';
  const pending = result.calculation_pending || [];
  const cards = [
    ['状态', status, String(status).startsWith('BLOCKED') ? 'bad' : 'good'],
    ['设备族', family, ''],
    ['型号状态', modelStatus, modelStatus === 'final_model' ? 'good' : 'warn'],
    ['待闭合计算', pending.length, pending.length ? 'warn' : 'good'],
  ];
  if (equipmentRecords.length > 1) cards.push(['Aspen 模块数', equipmentRecords.length, '']);
  $('result-summary').innerHTML = `<div class="summary-grid">${cards.map(c => `<div class="summary-card ${c[2]}"><small>${esc(c[0])}</small><strong>${esc(c[1])}</strong></div>`).join('')}</div><div class="boundary">多选时保留共同上位设备族/型式与候选集；只有证据唯一闭合后才下钻。LLM 不能覆盖硬门。</div>`;

  const equations = [];
  const appendFrom = (record) => {
    (record?.calculations || []).forEach(item => equations.push({text:item.equation_chain, pending:false}));
    (record?.calculation_pending || []).forEach(item => equations.push({text:`${item.calculation_id}：${item.status || '缺少 ' + (item.missing_fields || []).join(', ')}`, pending:true}));
  };
  if (equipmentRecords.length) equipmentRecords.forEach(item => appendFrom(item.match_result || item)); else appendFrom(result);
  $('equation-list').innerHTML = equations.length ? `<h3>公式链与待闭合项</h3>${equations.map(item => `<div class="equation ${item.pending ? 'pending' : ''}">${esc(item.text)}</div>`).join('')}` : '<h3>当前没有可闭合的计算链</h3>';
  $('raw-result').textContent = JSON.stringify(payload, null, 2);
}

async function bootstrap() {
  const response = await pywebview.api.bootstrap();
  if (!response.ok) return toast(response.error, true);
  const data = response.value;
  state.catalog = data.catalog;
  $('rule-status').textContent = `规则 ${data.catalog.rule_version}`;
  const com = data.com;
  $('com-status').textContent = com.available ? 'COM 可用' : 'COM 可选/未检测到';
  $('com-status').classList.toggle('warn', !com.available);
  $('com-unavailable-note').classList.toggle('hidden', com.available);
  const select = $('module-select');
  data.catalog.selections.forEach(item => {
    const option = document.createElement('option');
    option.value = item.selection_id;
    option.textContent = item.display_name;
    select.appendChild(option);
  });
  const pump = data.catalog.selections.find(item => item.block_type === 'PUMP');
  if (pump) select.value = pump.selection_id;
  renderManualFields();
  const skill = data.skill;
  $('skill-entry').innerHTML = `<strong>$${esc(skill.skill_name)}</strong><br>${esc(skill.prompt)}<br><br><b>Skill：</b>${esc(skill.global_skill_path)}<br><b>图谱：</b>${esc(skill.graph_entry)}`;
}

document.querySelectorAll('.mode-button').forEach(button => button.addEventListener('click', () => switchMode(button.dataset.mode)));
$('module-select').addEventListener('change', renderManualFields);
$('aspen-pressure-basis').addEventListener('change', event => $('atmospheric-wrap').classList.toggle('hidden', !['absolute', 'gauge'].includes(event.target.value)));

$('choose-aspen').addEventListener('click', async () => {
  const response = await pywebview.api.choose_aspen_file();
  if (!response.ok) return toast(response.error, true);
  if (response.value) $('aspen-path').value = response.value;
});

$('run-aspen').addEventListener('click', async () => {
  const path = $('aspen-path').value.trim();
  if (!path) return toast('请先选择 Aspen 文件。', true);
  const pressureBasis = $('aspen-pressure-basis').value;
  if (!['absolute', 'gauge'].includes(pressureBasis)) return toast('请选择 absolute（绝压）或 gauge（表压）；程序不会替你默认。', true);
  $('aspen-progress').classList.remove('hidden');
  $('run-aspen').disabled = true;
  const config = {
    source_path: path,
    pressure_basis: pressureBasis,
    atmospheric_pressure_mpa: $('aspen-atmospheric').value,
    timeout_s: Number($('aspen-timeout').value),
    run: $('aspen-run').checked,
  };
  const response = await pywebview.api.import_aspen(config);
  $('aspen-progress').classList.add('hidden');
  $('run-aspen').disabled = false;
  if (!response.ok) {
    if (response.value) renderResult(response.value);
    return toast(response.error || 'Aspen 自动导入失败；可切换手动或 LLM 模式。', true);
  }
  state.sessionDir = response.session_dir;
  $('session-actions').classList.toggle('hidden', !state.sessionDir);
  renderResult(response.value.result || response.value);
  toast('Aspen 模块遍历与逐台匹配完成。');
});

$('run-manual').addEventListener('click', async () => {
  const values = collectManualValues();
  const selectionId = $('module-select').value;
  const response = await pywebview.api.manual_match(selectionId, values);
  if (!response.ok) return toast(response.error, true);
  state.lastManual = {selectionId, values};
  renderResult(response.value);
  toast('确定性匹配与计算完成。');
});

$('run-llm').addEventListener('click', async () => {
  if (!state.lastResult) return toast('请先完成 Aspen 或手动确定性计算。', true);
  $('run-llm').disabled = true;
  const config = {
    api_key: $('llm-key').value,
    base_url: $('llm-base').value,
    model: $('llm-model').value,
    task: $('llm-task').value,
  };
  $('llm-key').value = '';
  const response = await pywebview.api.staged_hybrid_run(
    config,
    state.lastResult,
    {enabled: false},
    'audit',
    'minimum',
  );
  $('run-llm').disabled = false;
  if (!response.ok) return toast(response.error, true);
  const hybrid = response.value;
  state.llmProposal = hybrid.llm_review?.result || null;
  renderResult(hybrid);
  const hasChanges = state.llmProposal?.validated_proposal?.accepted_changes?.length > 0;
  $('apply-llm').classList.toggle('hidden', !hasChanges || !state.lastManual);
  toast(hasChanges ? 'LLM 提出了白名单决策；需人工确认后应用。' : 'LLM 审核完成，没有可自动应用的白名单决策。');
});

$('apply-llm').addEventListener('click', async () => {
  if (!state.lastManual || !state.llmProposal) return;
  const applied = await pywebview.api.apply_llm_proposal(state.lastManual.values, state.llmProposal);
  if (!applied.ok) return toast(applied.error, true);
  switchMode('manual');
  fillManualValues(applied.value);
  state.lastManual.values = applied.value;
  const response = await pywebview.api.manual_match(state.lastManual.selectionId, applied.value);
  if (!response.ok) return toast(response.error, true);
  renderResult({llm_applied_draft: applied.value, deterministic_recalculation: response.value});
  toast('白名单决策已应用到草稿，并由确定性脚本重新复算。');
});

$('run-kg').addEventListener('click', async () => {
  const query = $('kg-query').value.trim();
  if (!query) return;
  $('kg-result').textContent = '查询中…';
  const response = await pywebview.api.search_knowledge(query);
  if (!response.ok) return $('kg-result').textContent = response.error;
  $('kg-result').textContent = response.value.text || response.value.stderr || response.value.status;
});

$('save-result').addEventListener('click', async () => {
  if (!state.lastResult) return;
  const response = await pywebview.api.save_json(state.lastResult, 'equipment_design_result.json');
  if (!response.ok) return toast(response.error, true);
  if (response.value) toast(`已保存：${response.value}`);
});

$('open-session').addEventListener('click', async () => {
  if (!state.sessionDir) return;
  const response = await pywebview.api.open_folder(state.sessionDir);
  if (!response.ok) toast(response.error, true);
});

window.addEventListener('pywebviewready', bootstrap);
