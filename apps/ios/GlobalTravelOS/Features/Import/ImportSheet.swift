import SwiftUI
import UniformTypeIdentifiers

/// Excel 两阶段导入：选择文件 → 预览/勾选 → 确认落库。
///
/// This mirrors the backend contract exactly: the parse call **creates nothing**, and confirm is
/// the only write. Blocking rows are disabled in the picker; the server still re-validates.
struct ImportSheet: View {
    @EnvironmentObject private var environment: AppEnvironment
    @Environment(\.dismiss) private var dismiss

    let tripId: String
    let onImported: () async -> Void

    @State private var parseResult: ImportParseResult?
    @State private var selected: Set<Int> = []
    @State private var confirmResult: ConfirmImportResult?
    @State private var isParsing = false
    @State private var isConfirming = false
    @State private var showsPicker = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Button {
                        showsPicker = true
                    } label: {
                        Label(
                            isParsing ? "解析中…" : "选择 Excel 文件",
                            systemImage: "doc.badge.plus"
                        )
                    }
                    .disabled(isParsing || isConfirming)
                } footer: {
                    Text("解析阶段不写任何业务数据；确认后才会创建客户、地点与拜访。")
                }

                if let errorMessage {
                    Section { ErrorBanner(message: errorMessage) }
                }

                if let result = confirmResult {
                    Section("导入结果") {
                        Label(result.summaryText, systemImage: "checkmark.seal.fill")
                            .foregroundStyle(.green)
                        ForEach(result.warningList, id: \.self) { warning in
                            Text(warning)
                                .font(.caption)
                                .foregroundStyle(.orange)
                        }
                    }
                    Section {
                        Button("完成") {
                            dismiss()
                        }
                    }
                } else if let parse = parseResult {
                    Section("预览") {
                        Text(parse.summaryText)
                            .font(.subheadline)
                        if let missing = parse.missingColumns, !missing.isEmpty {
                            Text("缺少必需列：\(missing.joined(separator: "、"))")
                                .font(.caption)
                                .foregroundStyle(.red)
                        }
                        if let unknown = parse.unknownColumns, !unknown.isEmpty {
                            Text("忽略列：\(unknown.joined(separator: "、"))")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }

                    Section("逐行确认") {
                        ForEach(parse.rowList) { row in
                            rowView(row)
                        }
                    }

                    Section {
                        Button {
                            Task { await confirm(parse) }
                        } label: {
                            Label(
                                isConfirming
                                    ? "确认中…"
                                    : "确认导入 \(selected.count) 行",
                                systemImage: "square.and.arrow.down"
                            )
                        }
                        .disabled(isConfirming || selected.isEmpty)
                    }
                }
            }
            .navigationTitle("导入客户名单")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
            .fileImporter(
                isPresented: $showsPicker,
                allowedContentTypes: [
                    UTType(filenameExtension: "xlsx") ?? .data,
                    UTType(filenameExtension: "xlsm") ?? .data,
                ],
                allowsMultipleSelection: false
            ) { result in
                Task { await handleFile(result) }
            }
        }
    }

    @ViewBuilder
    private func rowView(_ row: ImportedRow) -> some View {
        HStack(alignment: .top) {
            Image(systemName: row.status.glyph)
                .foregroundStyle(color(for: row.status))
            VStack(alignment: .leading, spacing: 3) {
                Text("第 \(row.rowNumber) 行 · \(row.displayName)")
                    .font(.body)
                Text(row.status.label)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let issues = row.issueSummary {
                    Text(issues)
                        .font(.caption2)
                        .foregroundStyle(row.hasBlockingIssue ? .red : .orange)
                }
            }
            Spacer()
            Toggle(
                "",
                isOn: Binding(
                    get: { selected.contains(row.rowNumber) },
                    set: { isOn in
                        if isOn {
                            selected.insert(row.rowNumber)
                        } else {
                            selected.remove(row.rowNumber)
                        }
                    }
                )
            )
            .labelsHidden()
            .disabled(!row.status.selectableForImport)
        }
    }

    private func color(for status: ImportRowStatus) -> Color {
        switch status {
        case .valid: return .green
        case .invalid: return .red
        case .duplicate: return .orange
        case .unknown: return .gray
        }
    }

    private func handleFile(_ result: Result<[URL], Error>) async {
        errorMessage = nil
        do {
            guard let url = try result.get().first else { return }
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let data = try Data(contentsOf: url)

            isParsing = true
            defer { isParsing = false }
            let parsed = try await environment.tripRepository.parseImport(
                tripId: tripId,
                fileData: data,
                filename: url.lastPathComponent
            )
            parseResult = parsed
            selected = Set(parsed.importableRowNumbers)
            environment.refreshOfflineSummary()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private func confirm(_ parse: ImportParseResult) async {
        errorMessage = nil
        isConfirming = true
        defer { isConfirming = false }
        do {
            let result = try await environment.tripRepository.confirmImport(
                tripId: tripId,
                request: ConfirmImportRequest(
                    importId: parse.importId,
                    acceptedRowNumbers: selected.sorted()
                )
            )
            confirmResult = result
            await onImported()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}
