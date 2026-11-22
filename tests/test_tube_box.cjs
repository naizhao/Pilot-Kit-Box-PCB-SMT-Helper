const { test } = require("node:test");
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { readFile } = require("node:fs/promises");
const { join } = require("node:path");

const root = join(__dirname, "..");
const boards = {
  current: require("../data/v5.2.json"),
  legacy: require("../data/v4.12-pwr.json"),
};
const exact = boards.current.groups.find((group) => group.lcsc === "C307331");
const similar = boards.current.groups.find((group) => group.lcsc === "C14663");
const unnumbered = boards.current.groups.find((group) => group.spec === "10nF 50V C0G ±5%");
const higherVoltage = boards.current.groups.find((group) => group.parts.some((part) => part.ref === "C76"));
const shared = boards.legacy.groups.find((group) => group.lcsc === exact.lcsc);
const conflictingFields = boards.legacy.groups.find((group) => group.parts.some((part) => part.ref === "C77"));
const b1Spec = "22uF 16V X5R ±10%";

const origin = "https://assembly.example.test";
const allowed = /^\/(?:index\.html|i18n-en\.js|data\/[a-z0-9.-]+\.json)$/;

function serveStatic(context) {
  return context.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname === "/" ? "/index.html" : url.pathname;
    try {
      assert.equal(url.origin, origin);
      assert.equal(request.method(), "GET");
      assert.match(path, allowed);
      const body = await readFile(join(root, path));
      const type = path.endsWith(".html") ? "text/html" : path.endsWith(".js") ? "text/javascript" : "application/json";
      await route.fulfill({ contentType: type + ";charset=utf-8", body });
    } catch {
      await route.fulfill({ status: 404 });
    }
  });
}

test("tube box records physical parts, counts by keyboard and prints only essentials", async (t) => {
  const browser = await chromium.launch({ channel: "chrome" });
  t.after(() => browser.close());
  const context = await browser.newContext();
  await serveStatic(context);
  const page = await context.newPage();
  page.setDefaultTimeout(3000);
  await page.addInitScript(() => {
    window.print = () => {
      document.documentElement.dataset.printed = document.body.className;
      if (!window.deferAfterPrint) window.dispatchEvent(new Event("afterprint"));
    };
  });
  await page.goto(origin);

  const box = page.locator("#tubeBox");
  const cell = (position) => box.locator(`[data-tube-position="${position}"]`);
  const stored = () => page.evaluate(() => JSON.parse(localStorage.getItem("pk-assembly-tube-box")));
  const position = () => page.locator("#tubePosition").textContent();
  const message = () => page.locator("#tubeMessage").textContent();
  const active = () => page.evaluate(() => document.activeElement && document.activeElement.id);
  const fontSize = (selector) => page.locator(selector).evaluate((el) => parseFloat(getComputedStyle(el).fontSize));

  const fillNew = async (pos, kind, spec, size, lcsc, quantity) => {
    await cell(pos).click();
    await page.locator("#tubeKind").selectOption(kind);
    await page.locator("#tubeSpec").fill(spec);
    await page.locator("#tubeSize").fill(size);
    await page.locator("#tubeLcsc").fill(lcsc);
    await page.locator("#tubeQuantity").fill(quantity);
  };
  const pickBoard = (version) => page.locator("#cards .card").filter({ has: page.getByRole("heading", { name: version, exact: true }) }).click();
  const openPreparation = () => page.locator("#btnPrep").click();
  const openShared = async () => {
    await page.locator("#btnTubePicker").click();
    await box.waitFor({ state: "visible" });
    await page.locator("#tubeSource").getByText(/建议按 V5\.3/).waitFor();
  };
  const prepCard = async (group) => {
    const ref = group.parts[0].ref;
    await page.locator("#prepFilter").fill(ref);
    return page.locator(".prepCard").filter({ hasText: group.parts.map((part) => part.ref).join(" ") }).first();
  };

  await page.locator("#btnTubePicker").click();
  await box.waitFor({ state: "visible" });
  assert.equal(await page.locator("#tubeBom").count(), 0);
  assert.equal(await page.locator("#tubeBomBoard").count(), 0);
  assert.equal(await page.locator("#tubeUseSuggestion").count(), 0);
  assert.equal(await page.locator("#tubeBindingStatus").count(), 0);
  await page.locator("#tubeSource").getByText(/建议按 V5\.3/).waitFor();
  assert.equal(await box.locator("[data-tube-position]").count(), 100);
  assert.match(await page.locator("#tubeGridStatus").textContent(), /已登记 0 格/);
  assert.match(await page.locator("#tubeGridStatus").textContent(), /建议 71 格/);
  assert.match(await page.locator("#tubeGridStatus").textContent(), /备用 29 格/);
  assert.equal(await page.locator("#tubeQuantity").inputValue(), "");
  assert.equal(await page.locator("#tubeQuantity").getAttribute("placeholder"), "待盘点");
  assert.match(await page.locator("#tubeSlotDetails").textContent(), /建议/);
  assert.equal(await page.evaluate(() => localStorage.getItem("pk-assembly-tube-box")), null);

  // 空格按最新 V5.3 BOM 预填建议：先 C/R/L，再按封装，同一封装内按容量，和摆盘纸同序。
  assert.match(await cell("A1").getAttribute("aria-label"), /建议.*2\.7pF.*0402/);
  const suggestions = await page.locator("#tubeGrid [data-tube-position]").evaluateAll((nodes) =>
    nodes.filter((node) => node.classList.contains("suggested")).map((node) => {
      const [, spec, size] = node.getAttribute("aria-label").split(" · ");
      return { spec: spec.replace(/^建议 /, ""), size };
    }));
  assert.equal(suggestions.length, 71);
  // 片式小件也进管盒：ESD(0402)、LED(0805)、NTC(0603)，TPESD 在 BOM 里两行只占一格。
  const suggestedPositions = await page.locator("#tubeGrid [data-tube-position]").evaluateAll((nodes) =>
    nodes.filter((node) => node.classList.contains("suggested")).map((node) => node.getAttribute("aria-label")));
  assert.equal(suggestedPositions.filter((label) => /TPESD/.test(label)).length, 1);
  const chip = (needle) => suggestedPositions.find((label) => label.includes(needle)) || "";
  assert.match(chip("TPESD8L3.3"), /TPESD8L3\.3[\s\S]*· 0402 ·/);
  // LED 红、黄是两种料，各占一管
  assert.match(chip("LED 红"), /LED 红[\s\S]*· 0805 ·/);
  assert.match(chip("LED 黄"), /LED 黄[\s\S]*· 0805 ·/);
  assert.match(chip("NCP18XH103F03RB"), /NCP18XH103F03RB[\s\S]*· 0603 ·/);
  assert.ok(suggestedPositions.findIndex((label) => /TPESD/.test(label)) > suggestedPositions.findIndex((label) => /6\.8uH/.test(label)),
    "misc chip parts should follow the passives");
  const kindOf = (spec) => /^[0-9.]+[pnuµmkKMG]?F/i.test(spec) ? "C" : /^[0-9.]+[mkKMG]?Ω/.test(spec) ? "R" : /^[0-9.]+[pnuµmkKMG]?H/i.test(spec) ? "L" : "";
  const valueOf = (spec) => {
    const match = spec.match(/^([0-9.]+)\s*([pnuµmkKMG]?)\s*(?:F|Ω|H)/i);
    if (!match) return Infinity;
    const scale = { "": 0, p: -12, n: -9, u: -6, "µ": -6, "μ": -6, m: -3, k: 3, K: 3, M: 6, G: 6 }[match[2]];
    return Number(`${match[1]}e${scale}`);
  };
  // 不在 V5.3 BOM 里、但盒子还要留位的两管，按容量插在对应位置，不接在末尾。
  const itemIndex = (needle) => suggestions.findIndex(({ spec }) => spec.includes(needle));
  assert.ok(itemIndex("1.5kΩ") < itemIndex("3.32kΩ") && itemIndex("3.32kΩ") < itemIndex("4.7kΩ"),
    "3.32kΩ should sit between 1.5kΩ and 4.7kΩ");
  assert.ok(itemIndex("7.5nH") < itemIndex("18nH") && itemIndex("18nH") < itemIndex("27nH"),
    "18nH should sit between 7.5nH and 27nH");
  for (const kind of ["C", "R", "L"]) {
    const rows = suggestions.filter(({ spec }) => kindOf(spec) === kind)
      .map(({ spec, size }) => ({ package: /^\d{4}$/.test(size) ? Number(size) : 99999, value: valueOf(spec), size }));
    assert.ok(rows.length > 0, kind + " suggestions should exist");
    const packages = rows.map(({ package: size }) => size);
    assert.deepEqual(packages, [...packages].sort((a, b) => a - b), kind + " should group suggestions by package");
    for (let i = 1; i < rows.length; i++) {
      if (rows[i].package === rows[i - 1].package) {
        assert.ok(rows[i].value >= rows[i - 1].value,
          `${kind} ${rows[i - 1].size} ${rows[i - 1].value} should not come after ${rows[i].value}`);
      }
    }
  }
  await cell("A1").click();
  assert.equal(await active(), "tubeQuantity");
  assert.equal(await page.locator("#tubeSpec").inputValue(), "2.7pF 50V C0G ±0.25pF");
  assert.equal(await page.locator("#tubeSize").inputValue(), "0402");
  assert.equal(await page.locator("#tubeLcsc").inputValue(), "C1561");
  assert.ok(await fontSize("#tubeSpec") >= 20, "specification should be shown in a large font");
  assert.ok(await fontSize("#tubeSize") >= 16, "package should be shown in a large font");
  assert.ok(await fontSize("#tubeQuantity") >= 30, "quantity input should be the largest field");

  // 报错后不能把人锁在表单里：点其他管位照样切过去。
  await page.locator("#tubeSpec").fill("");
  await page.keyboard.press("Enter");
  assert.match(await message(), /请先填写规格/);
  await cell("A3").click();
  assert.equal(await position(), "A3");
  await cell("A1").click();
  assert.equal(await position(), "A1");

  await page.locator("#tubeKind").selectOption("C");
  await page.locator("#tubeSpec").fill(exact.spec);
  await page.locator("#tubeSize").fill(exact.size);
  await page.locator("#tubeLcsc").fill("c307331");
  await page.locator("#tubeSupplier").fill("淘宝·立创");
  await page.locator("#tubeQuantity").fill("500");
  await page.keyboard.press("Enter");
  assert.match(await message(), /已保存 A1/);
  assert.match(await message(), /下一管 A2/);
  const firstRecord = await stored();
  assert.deepEqual(firstRecord.A1, { kind: "C", spec: exact.spec, size: exact.size, lcsc: "C307331", mpn: "", supplier: "淘宝·立创", qty: 500 });
  assert.equal("binding" in firstRecord.A1, false);
  assert.equal(await position(), "A2");
  assert.match(await cell("A1").textContent(), /500/);

  await fillNew("A2", "C", unnumbered.spec, unnumbered.size, "", "350");
  await page.keyboard.press("Enter");
  await fillNew("B1", "C", b1Spec, higherVoltage.size, "C12891", "20");
  await page.keyboard.press("Enter");
  await fillNew("C1", "L", "6.2nH ±5%", "0402", "", "");
  await page.keyboard.press("Enter");
  await fillNew("C2", "R", "10kΩ 1%", "0603", "", "0");
  await page.keyboard.press("Enter");
  assert.equal(Object.keys(await stored()).length, 5);
  assert.match(await page.locator("#tubeGridStatus").textContent(), /已登记 5 格/);
  assert.match(await page.locator("#tubeGridStatus").textContent(), /已确认数量 4 格/);
  assert.match(await cell("A1").textContent(), /A1[\s\S]*100nF[\s\S]*0402/);
  assert.equal(await cell("C1").locator(".tubeSlotHead i").textContent(), "未知");
  assert.equal(await cell("C2").locator(".tubeSlotHead i").textContent(), "0");
  assert.equal(await cell("C2").locator(".tubeSlotSize").textContent(), "0603");
  assert.doesNotMatch(await box.locator("#tubeGrid").textContent(), /建议|未盘点|备用/);

  // 回车/方向键沿盒面顺序走：已入库的和建议摆位的都算一站，真空格跳过。
  await cell("A1").click();
  assert.equal(await active(), "tubeQuantity");
  await page.keyboard.press("Enter");
  assert.equal(await position(), "A2");
  assert.equal(await active(), "tubeQuantity");
  assert.equal(await page.locator("#tubeQuantity").inputValue(), "350");
  await page.keyboard.press("ArrowRight");
  assert.equal(await position(), "A3");
  assert.equal(await page.locator("#tubeQuantity").inputValue(), "");
  assert.match(await page.locator("#tubeSlotDetails").textContent(), /建议/);
  await page.keyboard.press("ArrowLeft");
  assert.equal(await position(), "A2");

  // Navigating saves a changed count instead of dropping it.
  await page.locator("#tubeQuantity").fill("351");
  await page.keyboard.press("ArrowRight");
  assert.equal(await position(), "A3");
  assert.equal((await stored()).A2.qty, 351);
  assert.match(await cell("A2").textContent(), /351/);

  // Clicking another grid slot also saves the pending count before switching.
  await cell("B1").click();
  await page.locator("#tubeQuantity").fill("21");
  await cell("C1").click();
  assert.equal(await position(), "C1");
  assert.equal((await stored()).B1.qty, 21);
  await cell("B1").click();

  // 无效的改动不会把人锁在表单里，也不会污染已存的记录。
  await page.locator("#tubeSpec").fill("");
  await page.locator("#tubeQuantity").focus();
  await page.keyboard.press("ArrowLeft");
  assert.notEqual(await position(), "C1");
  assert.equal((await stored()).B1.spec, b1Spec);
  await cell("B1").click();
  assert.equal(await page.locator("#tubeSpec").inputValue(), b1Spec);

  // 封装是必填的：备料要靠它找管位，没填就存不进去。
  await page.locator("#tubeSize").fill("");
  await page.keyboard.press("Enter");
  assert.match(await message(), /请先填写封装/);
  assert.equal((await stored()).B1.size, higherVoltage.size);

  // Registering a new tube with Enter keeps the newly saved count.
  await page.locator("#tubeFilter").fill("J10");
  await cell("J10").click();
  assert.equal(await active(), "tubeSpec");
  await page.locator("#tubeKind").selectOption("C");
  await page.locator("#tubeSpec").fill("47nF 50V X7R ±10%");
  await page.locator("#tubeSize").fill("0603");
  await page.locator("#tubeQuantity").fill("9");
  await page.keyboard.press("Enter");
  assert.equal((await stored()).J10.qty, 9);
  assert.equal("binding" in (await stored()).J10, false);
  await page.keyboard.press("ArrowLeft");
  const stepped = await position();
  assert.notEqual(stepped, "J10");
  assert.equal(await page.locator("#tubeFilter").inputValue(), "");
  assert.equal(await cell(stepped).getAttribute("aria-pressed"), "true");

  // Old records that still carry a BOM link keep working and lose the link on save.
  await page.evaluate((record) => {
    const data = JSON.parse(localStorage.getItem("pk-assembly-tube-box"));
    data.D4 = record;
    localStorage.setItem("pk-assembly-tube-box", JSON.stringify(data));
  }, { kind: "C", spec: "遗留 1nF", size: "0402", lcsc: "", mpn: "", qty: 3, binding: { board: "v5.2", group: "old" } });
  await page.locator("#tubeClose").click();
  await openShared();
  await page.locator("#tubeFilter").fill("D4");
  await cell("D4").click();
  assert.equal(await page.locator("#tubeQuantity").inputValue(), "3");
  await page.locator("#tubeQuantity").fill("4");
  await page.keyboard.press("Enter");
  assert.equal((await stored()).D4.qty, 4);
  assert.equal("binding" in (await stored()).D4, false);
  await page.locator("#tubeFilter").fill("");

  await page.reload();
  await pickBoard(boards.current.title);
  await openPreparation();
  const exactCard = await prepCard(exact);
  assert.match(await exactCard.locator(".tubePickup").textContent(), /A1[\s\S]*500 颗/);
  const similarCard = await prepCard(similar);
  assert.doesNotMatch(await similarCard.textContent(), /A1/);
  const unnumberedCard = await prepCard(unnumbered);
  assert.match(await unnumberedCard.locator(".tubePickup").textContent(), /A2[\s\S]*351 颗/);
  const higherVoltageCard = await prepCard(higherVoltage);
  assert.match(await higherVoltageCard.locator(".tubePickup").innerText(), /B1[\s\S]*耐压不同[\s\S]*16 V/);
  assert.equal(await page.getByRole("button", { name: /编码管盒|登记管位/ }).count(), 0);
  await page.locator("#btnPrepClose").click();
  await page.locator("#btnSheet").click();
  await page.locator("#sheet").getByRole("button", { name: higherVoltage.parts[0].ref, exact: true }).click();
  assert.match(await page.locator("#specTube").innerText(), /B1[\s\S]*耐压不同/);
  assert.match(await page.locator("#swapTube").innerText(), /B1[\s\S]*耐压不同/);
  // 备料清单打开时落在正在贴的料上：卡片高亮，卡片上的管位格号一起高亮。
  await openPreparation();
  const hereCard = await prepCard(higherVoltage);
  assert.equal(await hereCard.evaluate((el) => el.classList.contains("cur")), true);
  assert.match(await hereCard.locator(".tubePos").first().textContent(), /B1/);
  assert.equal(await hereCard.locator(".tubePos").first().evaluate((el) => getComputedStyle(el).color), "rgb(240, 160, 32)");
  await page.locator("#btnPrepClose").click();
  await page.locator("#btnBack").click();
  await openShared();

  // The tube box switches language with the rest of the page.
  await page.locator("#tubeBox .language-select").selectOption("en");
  assert.match(await page.locator("#tubeStepHelp").textContent(), /Enter saves and moves to the next tube/);
  assert.equal(await page.locator("#tubeQuantity").getAttribute("placeholder"), "Awaiting count");
  assert.match(await cell("A1").getAttribute("title"), /A1 · Capacitor/);
  assert.doesNotMatch(await cell("A1").getAttribute("title"), /电容|电阻|电感/);
  assert.match(await page.locator("#tubeGridStatus").textContent(), /7 recorded/);
  await page.locator("#tubeBox .language-select").selectOption("zh-CN");
  assert.match(await page.locator("#tubeStepHelp").textContent(), /Enter 保存并跳到下一管/);

  // Lid index: position, value, type and package only.
  await page.locator("#tubeShowPrint").click();
  // 盒盖 130 × 130 mm，A4 一张 1:1 打完，没有 A2/拼接选项。
  assert.equal(await page.locator("#tubePrintWidth").inputValue(), "130");
  assert.equal(await page.locator("#tubePrintHeight").inputValue(), "130");
  assert.equal(await page.locator("#tubePrintTiled").count(), 0);
  assert.equal(await page.locator("#tubePreview .tubePrintCell").count(), 100);
  assert.equal(await page.locator("#tubePreview .tubePrintPage").count(), 1);
  assert.match(await page.locator("#tubePrintWarning").textContent(), /A4 · 297 × 210 mm · 1 页 · 每格 13\.0 × 13\.0 mm/);
  const cellMm = await page.locator("#tubePreview .tubePrintCell").first().evaluate((el) => el.getBoundingClientRect().width * 25.4 / 96);
  assert.ok(Math.abs(cellMm - 13) < 0.3, `cell should lay out at 13 mm, got ${cellMm}`);
  const printed = (position) => page.locator(`#tubePreview .tubePrintCell[data-position="${position}"]`);
  assert.equal(await printed("A1").locator(".tubePrintValue").textContent(), "100nF");
  assert.equal(await printed("A1").locator(".tubePrintMeta").textContent(), "C · 0402");
  assert.equal(await printed("C2").locator(".tubePrintValue").textContent(), "10kΩ");
  assert.equal(await printed("C2").locator(".tubePrintMeta").textContent(), "R · 0603");
  assert.equal(await printed("C1").locator(".tubePrintValue").textContent(), "6.2nH");
  assert.equal(await printed("C1").locator(".tubePrintMeta").textContent(), "L · 0402");
  // 空格按默认建议摆位印出该放的料，仍然只印类型/封装/容量。
  assert.ok(await printed("A3").locator(".tubePrintValue").count() === 1, "spare suggested slot should print its value");
  assert.doesNotMatch(await printed("A3").textContent(), /颗|LCSC|建议|未盘点|备用/);
  const blank = page.locator("#tubePreview .tubePrintCell").filter({ hasNot: page.locator(".tubePrintValue") });
  assert.ok(await blank.count() > 0, "tubes beyond the suggested layout print only the position");
  assert.match(await blank.first().textContent(), /^[A-J](?:10|[1-9])$/);
  // 小件印型号而不是“容量”，类型/封装照旧。
  const positionOf = (needle) => page.locator("#tubeGrid [data-tube-position]").evaluateAll((nodes, text) => {
    const hit = nodes.find((node) => node.classList.contains("suggested") && node.getAttribute("aria-label").includes(text));
    return hit ? hit.dataset.tubePosition : "";
  }, needle);
  for (const [needle, value, meta] of [
    ["TPESD8L3.3", "0.3pF", "D · 0402"],
    ["LED 红", "LED 红", "D · 0805"],
    ["LED 黄", "LED 黄", "D · 0805"],
    ["NCP18XH103F03RB", "10k NTC", "RT · 0603"],
  ]) {
    const position = await positionOf(needle);
    assert.ok(position, needle + " should still be suggested");
    assert.equal(await printed(position).locator(".tubePrintValue").textContent(), value);
    assert.equal(await printed(position).locator(".tubePrintMeta").textContent(), meta);
  }
  assert.doesNotMatch(await page.locator("#tubePreview").textContent(), /颗|LCSC|建议|未盘点|备用|实盘/);

  const pdfPages = (pdf) => [...pdf.toString("latin1").matchAll(/\/Type\s*\/Page\b/g)].length;
  await page.evaluate(() => { window.deferAfterPrint = true; });
  const printToPdf = async () => {
    await page.locator("#tubeDoPrint").click();
    const pdf = await page.pdf({ preferCSSPageSize: true });
    await page.evaluate(() => window.dispatchEvent(new Event("afterprint")));
    return pdf;
  };
  assert.equal(pdfPages(await printToPdf()), 1);
  assert.match(await page.locator("#tubePrintMedia").textContent(), /size: 297mm 210mm/);
  // 比 A4 大的尺寸直接拒绝，不再偷偷缩放到纸宽。
  await page.locator("#tubePrintWidth").fill("400");
  await page.locator("#tubePrintHeight").fill("400");
  assert.equal(await page.locator("#tubeDoPrint").isDisabled(), true);
  assert.match(await page.locator("#tubePrintWarning").textContent(), /超出 A4 可打印范围/);
  await page.locator("#tubePrintWidth").fill("130");
  await page.locator("#tubePrintHeight").fill("130");
  assert.equal(await page.locator("#tubeDoPrint").isEnabled(), true);
  await page.locator("#tubePrintMode").selectOption("lid");
  assert.equal(await page.locator("#tubePrintWidth").inputValue(), "130");
  assert.equal(await page.locator("#tubePrintHeight").inputValue(), "130");
  assert.equal(await page.locator("#tubePreview .tubePrintCell").count(), 100);
  assert.equal(await page.locator("#tubePreview .tubePrintPage").count(), 1);
  await page.locator("#tubePrintMode").selectOption("labels");
  await page.locator("#tubePrintWidth").fill("28");
  await page.locator("#tubePrintHeight").fill("12");
  assert.equal(await page.locator("#tubePreview .tubePrintCell").count(), 7);
  await page.locator("#tubePrintWidth").fill("12");
  await page.locator("#tubePrintHeight").fill("4");
  assert.equal(await page.locator("#tubeDoPrint").isDisabled(), true);
  assert.match(await page.locator("#tubePrintWarning").textContent(), /文字放不下/);
  await page.locator("#tubePrintWidth").fill("28");
  await page.locator("#tubePrintHeight").fill("12");
  assert.equal(await page.locator("#tubeDoPrint").isEnabled(), true);
  assert.equal(pdfPages(await printToPdf()), 1);
  assert.match(await page.locator("html").getAttribute("data-printed"), /printingTubes/);
  await page.locator("#tubeClose").click();
  await pickBoard(boards.legacy.title);
  await openPreparation();
  assert.match(await (await prepCard(shared)).locator(".tubePickup").textContent(), /A1[\s\S]*500 颗/);
  assert.match(await (await prepCard(conflictingFields)).locator(".tubePickup").innerText(),
    /BOM 耐压字段不一致[\s\S]*规格 16 V[\s\S]*耐压栏 25 V[\s\S]*B1/);
  await page.locator("#btnPrepClose").click();
  await page.locator("#btnBack").click();
  await pickBoard(boards.current.title);
  await openPreparation();
  await page.locator("#btnPrepPrint").click();
  assert.doesNotMatch(await page.locator("html").getAttribute("data-printed"), /printingTubes/);
  assert.deepEqual(await page.locator("#printSheet .printPage").evaluateAll((pages) =>
    pages.map((page) => page.querySelectorAll(".cell").length)), [100, 18]);

  // 整盒清空：取消时不动，确认后回到默认建议摆位。
  await page.locator("#btnPrepClose").click();
  await page.locator("#btnBack").click();
  await openShared();
  const beforeReset = await page.evaluate(() => localStorage.getItem("pk-assembly-tube-box"));
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.locator("#tubeReset").click();
  assert.equal(await page.evaluate(() => localStorage.getItem("pk-assembly-tube-box")), beforeReset);
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("#tubeReset").click();
  await page.locator("#tubeMessage").getByText(/已清空整盒/).waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem("pk-assembly-tube-box")), "{}");
  assert.equal(await position(), "A1");
  assert.equal(await page.locator("#tubeSpec").inputValue(), "2.7pF 50V C0G ±0.25pF");
  assert.match(await page.locator("#tubeGridStatus").textContent(), /已登记 0 格/);
  assert.match(await page.locator("#tubeGridStatus").textContent(), /建议 71 格/);

  // 建议 BOM 读不到时退回手动登记，不影响管盒本身。
  const requests = [];
  const offlineContext = await browser.newContext();
  await offlineContext.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname === "/" ? "/index.html" : url.pathname;
    requests.push(path);
    if (/\/v5\.\d+\.json$/.test(path)) return route.fulfill({ status: 404 });
    try {
      const body = await readFile(join(root, path));
      await route.fulfill({ contentType: path.endsWith(".html") ? "text/html;charset=utf-8" : "application/json", body });
    } catch {
      await route.fulfill({ status: 404 });
    }
  });
  const offlinePage = await offlineContext.newPage();
  offlinePage.setDefaultTimeout(3000);
  await offlinePage.goto(origin);
  await offlinePage.locator("#btnTubePicker").click();
  await offlinePage.locator("#tubeBox").waitFor({ state: "visible" });
  await offlinePage.locator("#tubeSource").getByText(/无法载入建议 BOM/).waitFor();
  assert.deepEqual([...new Set(requests.filter((path) => /\/data\/v5\.\d+\.json$/.test(path)))], ["/data/v5.3.json"]);
  await offlinePage.locator('[data-tube-position="A1"]').click();
  await offlinePage.locator("#tubeSpec").fill("22pF 50V C0G ±5%");
  await offlinePage.locator("#tubeSize").fill("0402");
  await offlinePage.locator("#tubeQuantity").fill("8");
  await offlinePage.keyboard.press("Enter");
  assert.equal(await offlinePage.evaluate(() => JSON.parse(localStorage.getItem("pk-assembly-tube-box")).A1.qty), 8);
  await offlineContext.close();
});

test("local CSV previews and saves counts on a static HTTPS site without sending requests", async (t) => {
  const browser = await chromium.launch({ channel: "chrome" });
  t.after(() => browser.close());
  const context = await browser.newContext();
  await serveStatic(context);
  const page = await context.newPage();
  page.setDefaultTimeout(3000);
  const requests = [];
  page.on("request", (request) => requests.push([request.method(), new URL(request.url()).pathname, request.postData()]));
  await page.goto(origin);
  const oldStock = {
    A1: { kind: "C", spec: exact.spec, size: exact.size, lcsc: exact.lcsc, mpn: "原记录料号", qty: 12,
      binding: { board: "v5.2", group: "old" } },
    J9: { kind: "R", spec: "已有 10Ω", size: "0402", lcsc: "", mpn: "", qty: 11 },
  };
  await page.evaluate((stock) => localStorage.setItem("pk-assembly-tube-box", JSON.stringify(stock)), oldStock);
  await page.locator("#btnTubePicker").click();
  await page.locator("#tubeBox").waitFor({ state: "visible" });
  await page.locator("#tubeImport summary").click();

  const initialRequests = [...requests];
  const stored = () => page.evaluate(() => JSON.parse(localStorage.getItem("pk-assembly-tube-box")));
  const file = page.locator("#tubeImportFile");
  assert.match(await file.getAttribute("accept"), /^\.csv/);
  const selectCsv = (csv) => file.setInputFiles({ name: "库存.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: "下载 CSV 模板" }).click(),
  ]);
  assert.equal(download.suggestedFilename(), "管盒库存模板.csv");
  const template = await readFile(await download.path(), "utf8");
  assert.equal(template, "\uFEFF管位,类型,规格,封装,立创料号,厂家料号,供料商,本地库存\r\n");
  await selectCsv(template);
  await page.locator("#tubeImportStatus").getByText("0 行待核对").waitFor();
  const filledTemplate = template + "J5,电阻,82Ω,0603,,,淘宝,0\r\nJ4,电阻,91Ω,0603,,,,\r\n";
  await selectCsv(filledTemplate);
  const rows = page.locator("#tubeImportRows li");
  await rows.first().getByText(/表中管位 J5[\s\S]*0 颗/).waitFor();
  await rows.nth(1).getByText(/表中管位 J4[\s\S]*数量栏为空/).waitFor();
  assert.deepEqual(await stored(), oldStock);

  const quote = (value) => '"' + String(value ?? "").replaceAll('"', '""') + '"';
  const csv = [
    "位号,用量,类型,规格,封装,面,材质,耐压,品牌/型号,嘉立创料号,采购等级,规格要求,淘宝单价,淘宝采购量,嘉立创单价,单料总价,已购买,本地库存,嘉立创SMT,贴装归属,备注,管位,供料商",
    ...[
      ["C1,C2", "电容", exact.spec, exact.size, exact.lcsc, "0", "", "立创"],
      ["R10", "电阻", '<b>320Ω, "测试"\n注记</b>', "0603", "", "1,234", "", "淘宝店A"],
      ["L99", "电感", "特规电感", "1210", "", "12", "", ""],
      ["C99", "电容", "47nF 50V X7R", "0402", "", "0", "J10", ""],
      ["L98", "电感", "1uF", "0603", "", "16", "J8", ""],
      ["R98", "电阻", "20Ω", "0402", "", "24", "J9", ""],
      ["C97", "电容", "20uF 16V X7R", "1206", "", "-1", "J7", ""],
    ].map(([ref, kind, model, size, lcsc, count, position, supplier]) =>
      [ref, 3, kind, model, size, "", "", "", "合成品牌", lcsc, "", "", "", 75, "", "", 100, count, "", "", "", position, supplier].map(quote).join(",")),
  ].join("\r\n");
  await selectCsv("\uFEFF" + csv);
  await rows.nth(6).waitFor();
  assert.equal(await rows.count(), 7);
  assert.deepEqual(await stored(), oldStock);
  assert.match(await rows.nth(0).textContent(), /已有管位 A1[\s\S]*0 颗[\s\S]*12 颗/);
  assert.match(await rows.nth(1).textContent(), /<b>320Ω, "测试"\n注记<\/b>[\s\S]*自动分配 A2[\s\S]*1234 颗/);
  assert.equal(await rows.nth(1).locator("b").count(), 0);
  assert.match(await rows.nth(2).textContent(), /特规电感[\s\S]*自动分配 A3/);
  assert.match(await rows.nth(3).textContent(), /表中管位 J10[\s\S]*0 颗/);
  assert.match(await rows.nth(4).textContent(), /表中管位 J8[\s\S]*待核对元件类型/);
  assert.match(await rows.nth(5).textContent(), /表中管位 J9[\s\S]*已有不同物料/);
  assert.match(await rows.nth(6).textContent(), /数量格式无效/);
  for (const index of [4, 5, 6]) assert.equal(await rows.nth(index).getByRole("button").count(), 0);

  await rows.first().getByRole("button").click();
  assert.equal(await page.locator("#tubePosition").textContent(), "A1");
  assert.equal(await page.locator("#tubeMpn").inputValue(), "原记录料号");
  assert.equal(await page.locator("#tubeSupplier").inputValue(), "立创");
  assert.equal(await page.locator("#tubeQuantity").inputValue(), "0");
  assert.deepEqual(await stored(), oldStock);

  const batch = page.locator("#tubeImportApply");
  const confirmBatch = page.locator("#tubeImportConfirm");
  await batch.getByText(/导入 4 条/).waitFor();
  await batch.click();
  await confirmBatch.getByText(/确认导入 4 条/).waitFor();
  await page.locator("#tubeImportCancel").click();
  assert.deepEqual(await stored(), oldStock);

  const concurrent = await context.newPage();
  await concurrent.goto(origin);
  await batch.click();
  await concurrent.evaluate(() => {
    const data = JSON.parse(localStorage.getItem("pk-assembly-tube-box"));
    data.J10 = { kind: "R", spec: "并行盘点 20Ω", size: "0402", lcsc: "", mpn: "", qty: 8 };
    localStorage.setItem("pk-assembly-tube-box", JSON.stringify(data));
  });
  await page.locator('[data-tube-position="J10"][aria-label*="并行盘点 20Ω"]').waitFor();
  await confirmBatch.click();
  await page.locator("#tubeImportStatus").getByText(/库存已变化/).waitFor();
  assert.equal((await stored()).A1.qty, 12);
  assert.equal((await stored()).J10.qty, 8);
  assert.equal((await stored()).A2, undefined);

  await concurrent.evaluate(() => {
    const data = JSON.parse(localStorage.getItem("pk-assembly-tube-box"));
    delete data.J10;
    localStorage.setItem("pk-assembly-tube-box", JSON.stringify(data));
    window.importWrites = 0;
    window.addEventListener("storage", (event) => {
      if (event.key === "pk-assembly-tube-box") window.importWrites++;
    });
  });
  await batch.getByText(/导入 4 条/).waitFor();
  await batch.click();
  await confirmBatch.click();
  await page.locator("#tubeImportStatus").getByText(/已导入 4 条/).waitFor();
  const updated = await stored();
  assert.deepEqual(updated.A1, { kind: "C", spec: exact.spec, size: exact.size, lcsc: exact.lcsc, mpn: "原记录料号", qty: 0, supplier: "立创" });
  assert.deepEqual(updated.J9, oldStock.J9);
  assert.equal(updated.A2.spec, '<b>320Ω, "测试" 注记</b>');
  assert.equal(updated.A2.supplier, "淘宝店A");
  assert.equal(updated.A3.supplier, "");
  assert.equal(updated.A2.qty, 1234);
  assert.equal("binding" in updated.A2, false);
  assert.equal(updated.A3.qty, 12);
  assert.equal(updated.J10.qty, 0);
  assert.equal(updated.J8, undefined);
  assert.equal(updated.J7, undefined);
  await concurrent.waitForFunction(() => window.importWrites === 1);
  await concurrent.close();

  await selectCsv("类型,规格,封装,嘉立创料号,本地库存\n电容," + quote(exact.spec) + "," + exact.size + "," + exact.lcsc + ",");
  await rows.first().getByText(/保留已有数量/).waitFor();
  assert.equal(await rows.first().getByRole("button").count(), 0);
  assert.equal((await stored()).A1.qty, 0);

  await selectCsv("管位,规格,封装,数量\nJ6,68Ω,0603,19\n,47Ω,0603,900");
  await rows.first().getByText(/表中管位 J6[\s\S]*19 颗/).waitFor();
  await rows.nth(1).getByText(/通用数量须绑定明确管位/).waitFor();
  assert.equal(await rows.nth(1).getByRole("button").count(), 0);
  await rows.first().getByRole("button").click();
  await page.keyboard.press("Enter");
  assert.equal((await stored()).J6.qty, 19);

  await selectCsv("类型,规格,封装,嘉立创料号,本地库存\n电容,99nF,0402," + exact.lcsc + ",29");
  await rows.first().getByText(/同料号的已有记录需核对/).waitFor();
  assert.equal(await rows.first().getByRole("button").count(), 0);
  assert.equal((await stored()).A1.qty, 0);

  await selectCsv("Designator,Comment,Footprint,Quantity\nR1,330Ω,0603,50");
  await page.locator("#tubeImportStatus").getByText(/实际库存列/).waitFor();
  assert.equal(await rows.count(), 0);

  await selectCsv("类型,规格,当前单价,候选库存,当前库存,淘宝采购量,已购买\n电阻,330Ω,1,250,200,75,100");
  await page.locator("#tubeImportStatus").getByText(/实际库存列/).waitFor();
  assert.equal(await rows.count(), 0);
  await selectCsv('类型,规格,本地库存\n电阻,"未闭合,7');
  await page.locator("#tubeImportStatus").getByText(/CSV 引号格式无效/).waitFor();
  assert.equal(await rows.count(), 0);

  await selectCsv(filledTemplate);
  await batch.getByText(/导入 1 条/).waitFor();
  await batch.click();
  await confirmBatch.click();
  await page.locator("#tubeImportStatus").getByText(/已导入 1 条/).waitFor();
  const importedTemplate = await stored();
  assert.equal(importedTemplate.J5.qty, 0);
  assert.equal(importedTemplate.J4, undefined);
  assert.deepEqual(requests, initialRequests);
  assert.deepEqual(await context.cookies(), []);
  await page.reload();
  await page.locator("#btnTubePicker").click();
  assert.match(await page.locator('[data-tube-position="A1"]').textContent(), /0/);
  assert.match(await page.locator('[data-tube-position="J10"]').textContent(), /0/);
  assert.equal((await stored()).J9.qty, 11);
});
