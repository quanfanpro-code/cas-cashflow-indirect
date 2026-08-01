import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";


const [
  payloadPath,
  outputPath,
  nodeModulesPath,
  runtimeDir,
  previewDir,
] = process.argv.slice(2);

if (![payloadPath, outputPath, nodeModulesPath, runtimeDir, previewDir].every(Boolean)) {
  throw new Error("工作簿生成参数不完整");
}

await fs.mkdir(runtimeDir, { recursive: true });
const nodeModulesLink = path.join(runtimeDir, "node_modules");
try {
  await fs.lstat(nodeModulesLink);
} catch {
  await fs.symlink(nodeModulesPath, nodeModulesLink, "junction");
}

const runtimeRequire = createRequire(
  pathToFileURL(path.join(runtimeDir, "artifact-entry.cjs")),
);
const { SpreadsheetFile, Workbook } = runtimeRequire("@oai/artifact-tool");
const payloadText = (await fs.readFile(payloadPath, "utf8")).replace(/^\uFEFF/, "");
const payload = JSON.parse(payloadText);
const workbook = Workbook.create();

for (const sheetPayload of payload.sheets) {
  const sheet = workbook.worksheets.add(sheetPayload.name);
  sheet.showGridLines = false;
  const rows = sheetPayload.rows;
  const columnCount = Math.max(1, ...rows.map((row) => row.length));
  const paddedRows = rows.map((row) => [
    ...row,
    ...Array(columnCount - row.length).fill(null),
  ]);
  const used = sheet.getRangeByIndexes(0, 0, paddedRows.length, columnCount);
  used.values = paddedRows;
  used.format = {
    font: { name: "Microsoft YaHei", size: 10, color: "#1F2937" },
    verticalAlignment: "center",
    wrapText: true,
  };

  const header = sheet.getRangeByIndexes(0, 0, 1, columnCount);
  header.format = {
    fill: "#17365D",
    font: {
      name: "Microsoft YaHei",
      size: 10,
      bold: true,
      color: "#FFFFFF",
    },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "outside", style: "thin", color: "#17365D" },
  };
  header.format.rowHeight = 26;
  sheet.freezePanes.freezeRows(1);

  for (let column = 0; column < columnCount; column += 1) {
    const headerText = String(paddedRows[0][column] ?? "");
    const columnValues = paddedRows.map((row) => String(row[column] ?? ""));
    const visualLength = (value) => [...value].reduce(
      (total, character) => total + (/[\u2E80-\u9FFF]/.test(character) ? 2 : 1),
      0,
    );
    const longest = Math.max(
      visualLength(headerText),
      ...columnValues.map(visualLength),
    );
    const columnRange = sheet.getRangeByIndexes(0, column, paddedRows.length, 1);
    columnRange.format.columnWidth = Math.min(42, Math.max(12, longest + 4));
    if (/金额|余额|差异|现金|损益|占用/.test(headerText)) {
      const numericRange = sheet.getRangeByIndexes(
        1,
        column,
        Math.max(1, paddedRows.length - 1),
        1,
      );
      numericRange.format.numberFormat = "#,##0.00;[Red](#,##0.00);-";
      numericRange.format.horizontalAlignment = "right";
    }
  }
  used.format.autofitRows();
  header.format.rowHeight = 30;

  if (sheetPayload.name === "附表结果" && paddedRows.length > 2) {
    sheet.getRangeByIndexes(1, 0, paddedRows.length - 1, 1).format.font = {
      name: "Microsoft YaHei",
      size: 10,
      color: "#1F2937",
    };
    const finalRows = sheet.getRangeByIndexes(
      Math.max(1, paddedRows.length - 5),
      0,
      Math.min(5, paddedRows.length - 1),
      columnCount,
    );
    finalRows.format = {
      fill: "#D9EAF7",
      font: {
        name: "Microsoft YaHei",
        size: 10,
        bold: true,
        color: "#17365D",
      },
      borders: { preset: "outside", style: "thin", color: "#9EADBA" },
    };
  }
}

const summaryInspect = await workbook.inspect({
  kind: "table",
  range: "附表结果!A1:H25",
  include: "values,formulas",
  tableMaxRows: 25,
  tableMaxCols: 8,
  maxChars: 6000,
});
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "最终公式错误扫描",
  maxChars: 3000,
});

await fs.mkdir(previewDir, { recursive: true });
const previews = [];
for (const sheetPayload of payload.sheets) {
  const preview = await workbook.render({
    sheetName: sheetPayload.name,
    autoCrop: "all",
    scale: 1,
    format: "png",
  });
  const previewPath = path.join(previewDir, `${sheetPayload.name}.png`);
  await fs.writeFile(
    previewPath,
    new Uint8Array(await preview.arrayBuffer()),
  );
  previews.push(previewPath);
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const result = await SpreadsheetFile.exportXlsx(workbook);
await result.save(outputPath);
process.stdout.write(
  JSON.stringify({
    summaryInspect: summaryInspect.ndjson,
    formulaErrors: formulaErrors.ndjson,
    previews,
  }),
);
