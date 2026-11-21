import SwiftUI

/// 客户库（审计 5.5）：搜索、列表、新建。云端走 API，本地读写 SQLite。
struct CustomersView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: CustomersViewModel

    init(environment: AppEnvironment) {
        _viewModel = StateObject(wrappedValue: CustomersViewModel(environment: environment))
    }

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.customers.isEmpty && !viewModel.isLoading {
                    ContentUnavailableView(
                        "客户库为空",
                        systemImage: "person.2",
                        description: Text("点击右上角 + 新建客户；客户也会随 Excel 导入进入。")
                    )
                } else {
                    List {
                        if let error = viewModel.errorMessage {
                            Section { Text(error).font(.caption).foregroundStyle(.red) }
                        }
                        ForEach(viewModel.customers) { customer in
                            NavigationLink {
                                CustomerDetailView(customer: customer)
                            } label: {
                                VStack(alignment: .leading, spacing: 3) {
                                    HStack {
                                        Text(customer.name).font(.body)
                                        Spacer()
                                        DSBadge(
                                            text: customer.tier,
                                            color: DS.ColorToken.tier(customer.tier)
                                        )
                                    }
                                    HStack(spacing: 8) {
                                        if let industry = customer.industry, !industry.isEmpty {
                                            Text(industry)
                                        }
                                        if let country = customer.countryCode {
                                            Text(country)
                                        }
                                        if let contact = customer.contactSummary {
                                            Text(contact).lineLimit(1)
                                        }
                                    }
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                }
                            }
                        }
                    }
                    .searchable(text: $viewModel.query, prompt: "按名称检索")
                    .onSubmit(of: .search) {
                        Task { await viewModel.load() }
                    }
                    .onChange(of: viewModel.query) { _, _ in
                        Task { await viewModel.load() }
                    }
                }
            }
            .navigationTitle("客户")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        viewModel.showsCreate = true
                    } label: {
                        Image(systemName: "plus")
                    }
                    .accessibilityLabel("新建客户")
                }
            }
            .refreshable { await viewModel.load() }
            .task { await viewModel.load() }
            .sheet(isPresented: $viewModel.showsCreate) {
                CustomerCreateSheet(onCreate: { request in
                    await viewModel.create(request)
                })
            }
        }
    }
}

@MainActor
final class CustomersViewModel: ObservableObject {
    @Published private(set) var customers: [Customer] = []
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?
    @Published var query = ""
    @Published var showsCreate = false

    private let environment: AppEnvironment

    init(environment: AppEnvironment) {
        self.environment = environment
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let page = try await environment.customerRepository.listCustomers(
                query: query.isEmpty ? nil : query, page: PageRequest(limit: 100)
            )
            customers = page.items
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func create(_ request: CustomerCreateRequest) async -> Bool {
        do {
            let customer = try await environment.customerRepository.createCustomer(request)
            customers.removeAll { $0.id == customer.id }
            customers.insert(customer, at: 0)
            return true
        } catch {
            errorMessage = environment.errorMessage(error)
            return false
        }
    }
}

struct CustomerDetailView: View {
    let customer: Customer

    var body: some View {
        List {
            Section("基本信息") {
                LabeledContent("名称", value: customer.name)
                LabeledContent("分级", value: customer.tierLabel)
                if let industry = customer.industry { LabeledContent("行业", value: industry) }
                if let country = customer.countryCode { LabeledContent("国家", value: country) }
                if let ref = customer.externalRef { LabeledContent("外部编号", value: ref) }
                LabeledContent("状态", value: customer.isActive ? "活跃" : "停用")
            }
            Section("主要联系人") {
                if let name = customer.primaryContactName { LabeledContent("姓名", value: name) }
                if let email = customer.primaryContactEmail { LabeledContent("邮箱", value: email) }
                if let phone = customer.primaryContactPhone { LabeledContent("电话", value: phone) }
                if customer.primaryContactName == nil,
                   customer.primaryContactEmail == nil,
                   customer.primaryContactPhone == nil {
                    Text("未登记联系人").foregroundStyle(.secondary)
                }
            }
            CustomerAddressBookSection(customerId: customer.id)
        }
        .navigationTitle(customer.name)
    }
}

struct CustomerCreateSheet: View {
    @Environment(\.dismiss) private var dismiss
    let onCreate: (CustomerCreateRequest) async -> Bool

    @State private var name = ""
    @State private var tier = "C"
    @State private var industry = ""
    @State private var countryCode = ""
    @State private var contactName = ""
    @State private var contactEmail = ""
    @State private var contactPhone = ""
    @State private var saving = false

    var body: some View {
        NavigationStack {
            Form {
                Section("客户") {
                    TextField("客户名称", text: $name)
                    Picker("分级", selection: $tier) {
                        Text("A（战略）").tag("A")
                        Text("B（重点）").tag("B")
                        Text("C（一般）").tag("C")
                    }
                    TextField("行业", text: $industry)
                    TextField("国家代码（如 DE）", text: $countryCode)
                        .textInputAutocapitalization(.characters)
                }
                Section("主要联系人") {
                    TextField("姓名", text: $contactName)
                    TextField("邮箱", text: $contactEmail)
                        .keyboardType(.emailAddress)
                        .textInputAutocapitalization(.never)
                    TextField("电话", text: $contactPhone)
                        .keyboardType(.phonePad)
                }
            }
            .navigationTitle("新建客户")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        saving = true
                        Task {
                            let ok = await onCreate(
                                CustomerCreateRequest(
                                    name: name,
                                    tier: tier,
                                    countryCode: countryCode.isEmpty ? nil : countryCode.uppercased(),
                                    industry: industry.isEmpty ? nil : industry,
                                    primaryContactName: contactName.isEmpty ? nil : contactName,
                                    primaryContactEmail: contactEmail.isEmpty ? nil : contactEmail,
                                    primaryContactPhone: contactPhone.isEmpty ? nil : contactPhone
                                )
                            )
                            saving = false
                            if ok { dismiss() }
                        }
                    }
                    .disabled(saving || name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
    }
}
