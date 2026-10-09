'use strict';

const { App } = require('@slack/bolt');
const {
  MACHINE_INFO,
  parseMemoText,
  generateOutput,
  checkMissing,
  getStoreRate,
} = require('./formatter_core');

const SLACK_BOT_TOKEN = requiredEnv('SLACK_BOT_TOKEN');
const SLACK_APP_TOKEN = requiredEnv('SLACK_APP_TOKEN');

// Current ECP pilot channels. Environment variables can override these later.
const SOURCE_CHANNEL_ID = process.env.ECP_SOURCE_CHANNEL_ID || 'C0C7WHETTC6'; // #原
const OUTPUT_CHANNEL_ID = process.env.ECP_OUTPUT_CHANNEL_ID || 'C0C8STM7WDN'; // #原データ作成
const TEST_CHANNEL_ID = process.env.ECP_TEST_CHANNEL_ID || 'C0C7SSG07L5';

const MACHINE_ALIASES = [
  ['karakuri2', ['からサー2', 'からサー', 'からくりサーカス2', 'からくり']],
  ['monkey', ['モンキーターン', 'モンキー']],
  ['tensei', ['転生']],
  ['ghoul', ['東京喰種', '喰種']],
  ['tekken', ['鉄拳']],
  ['god', ['ミリオンゴッド', 'ゴッド', 'GOD']],
  ['enen', ['炎炎']],
  ['madomagi', ['まどマギ', 'マギレコ']],
  ['tokyorev', ['東京リベンジャーズ', '東リべ', '東リベ']],
  ['sao', ['SAO']],
  ['rikoriko', ['リコリスリコイル', 'リコリス・リコイル', 'リコリス', 'リコリコ']],
  ['hokuto', ['北斗の拳', '北斗']],
];

const pendingStarts = new Map();
const processedEvents = new Set();

function requiredEnv(name) {
  const value = String(process.env[name] || '').trim();
  if (!value) throw new Error(`Missing environment variable: ${name}`);
  return value;
}

function normalizedLines(text) {
  return String(text || '')
    .replace(/\r/g, '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

function isStartReport(text) {
  const lines = normalizedLines(text);
  const hasMachineNumber = lines.some((line) => /^台番\s*\d+/.test(line));
  const hasStart = lines.some((line) => /^始め(?:[（(]データランプ[）)])?\s*-?\d+/.test(line));
  const hasEndMarker = lines.some((line) => /^抜け\s*[\d#]+/.test(line));
  return hasMachineNumber && hasStart && !hasEndMarker;
}

function isEndReport(text) {
  const lines = normalizedLines(text);
  const hasOut = lines.some((line) => /^抜け\s*[\d#]+/.test(line));
  const hasGet = lines.some((line) => /^獲得(?:\s*[\d#]+)?$/.test(line));
  const hasStoreOrName = lines.some((line) => /^(?:店名|名前)\s*.+/.test(line));
  return hasOut && hasGet && hasStoreOrName;
}

function detectMachine(text) {
  const firstLine = normalizedLines(text)[0] || '';
  for (const [key, aliases] of MACHINE_ALIASES) {
    if (aliases.some((alias) => firstLine === alias || firstLine.startsWith(`${alias} `) || firstLine.startsWith(`${alias}　`))) {
      return key;
    }
  }
  return null;
}

async function findLatestStartFromHistory(client, userId, beforeTs) {
  let cursor;
  // Search up to 3 pages (300 messages) to survive restarts without a database.
  for (let page = 0; page < 3; page += 1) {
    const response = await client.conversations.history({
      channel: SOURCE_CHANNEL_ID,
      latest: beforeTs,
      inclusive: false,
      limit: 100,
      cursor,
    });

    for (const message of response.messages || []) {
      if (message.user !== userId) continue;
      if (message.bot_id || message.subtype) continue;
      if (isStartReport(message.text || '')) {
        return { text: message.text || '', ts: message.ts || '' };
      }
    }

    cursor = response.response_metadata?.next_cursor;
    if (!cursor) break;
  }
  return null;
}

async function replyError(say, event, message) {
  await say({ text: `⚠️ ECP-Bot: ${message}`, thread_ts: event.ts });
}

function buildOutput(startText, endText) {
  const machine = detectMachine(startText);
  if (!machine || !MACHINE_INFO[machine]) {
    return { error: '開始報告の機種名を判定できませんでした。' };
  }

  const memo = `${startText.trim()}\n${endText.trim()}`;
  const parsed = parseMemoText(memo, machine);
  const missing = checkMissing(parsed, machine);
  if (missing.length) {
    return { error: `抜けシート生成に必要な項目が不足しています：${missing.join('、')}` };
  }

  const rate = getStoreRate(parsed.shop);
  if (!rate) {
    return { error: `店舗「${parsed.shop || '未入力'}」の貸玉・換金率がフォーマッターに登録されていません。` };
  }

  // Existing formatter behavior: when 貯玉 is 0, output blank; otherwise use the report's 貯玉 value.
  const prevVal = parsed.chodama === '0' ? '' : (parsed.chodama || '');
  const output = generateOutput(machine, parsed, {
    shop: parsed.shop || '',
    name: parsed.name || '',
    kinsen1: '1000',
    kinsen2: rate[1],
    kashi1: '1000',
    kashi2: rate[0],
    prevVal,
    manualKitai: '',
  });

  return { output, machine, parsed };
}

const app = new App({
  token: SLACK_BOT_TOKEN,
  appToken: SLACK_APP_TOKEN,
  socketMode: true,
});

app.event('message', async ({ event, client, say, logger }) => {
  try {
    if (event.subtype || event.bot_id || !event.user || !event.ts) return;

    // Slack may retry event deliveries. Avoid duplicate output during one process lifetime.
    if (processedEvents.has(event.ts)) return;
    processedEvents.add(event.ts);
    if (processedEvents.size > 500) {
      const first = processedEvents.values().next().value;
      processedEvents.delete(first);
    }

    const text = String(event.text || '').trim();

    // Keep the original connectivity test alive.
    if (event.channel === TEST_CHANNEL_ID && text === 'テスト') {
      await say({ text: 'ECP-Bot、正常に稼働中です！🤖', thread_ts: event.ts });
      return;
    }

    if (event.channel !== SOURCE_CHANNEL_ID) return;

    if (isStartReport(text)) {
      pendingStarts.set(event.user, { text, ts: event.ts });
      logger.info(`ECP start report stored: user=${event.user}, machine=${detectMachine(text) || 'unknown'}`);
      return;
    }

    // Intermediate notes such as "159GG" or "現金再開6000〜215" are intentionally ignored.
    if (!isEndReport(text)) return;

    let start = pendingStarts.get(event.user) || null;
    if (!start || Number(start.ts) >= Number(event.ts)) {
      start = await findLatestStartFromHistory(client, event.user, event.ts);
    }

    if (!start) {
      await replyError(say, event, 'この抜け報告より前の開始報告が見つかりませんでした。');
      return;
    }

    const result = buildOutput(start.text, text);
    if (result.error) {
      await replyError(say, event, result.error);
      return;
    }

    await client.chat.postMessage({
      channel: OUTPUT_CHANNEL_ID,
      text: result.output,
    });

    pendingStarts.delete(event.user);
    logger.info(`ECP sheet posted: user=${event.user}, machine=${result.machine}, end_ts=${event.ts}`);
  } catch (error) {
    logger.error(error);
    try {
      if (event.channel === SOURCE_CHANNEL_ID && event.ts) {
        await say({ text: '⚠️ ECP-Bot内部でエラーが発生しました。Railwayのログを確認してください。', thread_ts: event.ts });
      }
    } catch (_) {}
  }
});

(async () => {
  await app.start();
  console.log('⚡ ECP-Bot v2 is running');
  console.log(`#原: ${SOURCE_CHANNEL_ID} -> #原データ作成: ${OUTPUT_CHANNEL_ID}`);
})();

module.exports = { isStartReport, isEndReport, detectMachine, buildOutput };
