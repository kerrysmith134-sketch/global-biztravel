import Foundation

struct LocalGuideSection: Codable, Equatable, Identifiable {
    var key: String
    var title: String
    var items: [String]?

    var id: String { key }
    var itemList: [String] { items ?? [] }
}

struct LocalGuideCity: Codable, Equatable, Identifiable {
    var city: String
    var tips: [String]?

    var id: String { city }
    var tipList: [String] { tips ?? [] }
}

struct LocalGuideCountry: Codable, Equatable, Identifiable {
    var countryCode: String
    var name: String
    var sections: [LocalGuideSection]?
    var cities: [LocalGuideCity]?

    var id: String { countryCode }
    var sectionList: [LocalGuideSection] { sections ?? [] }
    var cityList: [LocalGuideCity] { cities ?? [] }
}

struct LocalGuideDataGap: Codable, Equatable, Identifiable {
    var code: String
    var message: String
    var countryCode: String?

    var id: String { code + (countryCode ?? "") }
}

struct LocalGuide: Codable, Equatable {
    var tripId: String
    var countries: [LocalGuideCountry]?
    var citiesCovered: [String]?
    var dataGaps: [LocalGuideDataGap]?

    var countryList: [LocalGuideCountry] { countries ?? [] }
    var gapList: [LocalGuideDataGap] { dataGaps ?? [] }
}
