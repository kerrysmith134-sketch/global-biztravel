import Foundation

/// 本地模式当地指南（P7）：离线目录随包，按出行国家与拜访城市组装。
/// 实时源（餐厅推荐/天气/POI）未接入时以 data_gaps 显式标注——与服务端同语义。
enum LocalGuideBuilder {
    struct Catalog {
        var countries: [String: [String: Any]]
    }

    static func loadCatalog() -> Catalog? {
        guard let url = Bundle.main.url(forResource: "local_guide_catalog", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let countries = object["countries"] as? [String: [String: Any]] else {
            return nil
        }
        return Catalog(countries: countries)
    }

    static func build(database: LocalDatabase, tripId: String) throws -> LocalGuide {
        let tripPayloads = try database.loadPayloads(table: "trips", where: "id = ?", bindings: [tripId])
        guard let tripPayload = tripPayloads.first else {
            throw APIError.serverError(code: "NOT_FOUND", message: "出行不存在", traceId: nil, details: [:])
        }
        let trip = try DateCoding.decoder().decode(Trip.self, from: tripPayload)

        // 拜访城市（去重排序）
        var cities: [String] = []
        let visitPayloads = try database.loadPayloads(table: "visits", where: "trip_id = ?", bindings: [tripId])
        for payload in visitPayloads {
            guard let visit = try? DateCoding.decoder().decode(TripVisit.self, from: payload),
                  let placeId = visit.placeId,
                  let placePayload = try database.loadPayloads(
                      table: "places", where: "id = ?", bindings: [placeId]
                  ).first,
                  let place = try? DateCoding.decoder().decode(Place.self, from: placePayload),
                  let city = place.city, !city.isEmpty else { continue }
            if !cities.contains(city) { cities.append(city) }
        }
        cities.sort()

        guard let catalog = loadCatalog() else {
            return LocalGuide(
                tripId: tripId,
                countries: [],
                citiesCovered: cities,
                dataGaps: [
                    LocalGuideDataGap(code: "CATALOG_MISSING", message: "离线指南目录未随包打包", countryCode: nil)
                ]
            )
        }

        var countries: [LocalGuideCountry] = []
        var gaps: [LocalGuideDataGap] = [
            LocalGuideDataGap(
                code: "LIVE_POI_NOT_CONNECTED",
                message: "实时餐厅/景点/POI 推荐源未接入，本指南只提供固定的商务与生活要点。",
                countryCode: nil
            ),
            LocalGuideDataGap(
                code: "WEATHER_NOT_CONNECTED",
                message: "天气与中断信息需接入公开数据源后才会出现在指南里。",
                countryCode: nil
            ),
        ]

        for code in (trip.countries ?? []).map({ $0.uppercased() }) {
            guard let entry = catalog.countries[code] else {
                gaps.append(
                    LocalGuideDataGap(
                        code: "COUNTRY_GUIDE_MISSING",
                        message: "目录暂无 \(code) 的当地指南条目。",
                        countryCode: code
                    )
                )
                continue
            }
            let rawSections = entry["sections"] as? [[String: Any]] ?? []
            let sections = rawSections.compactMap { section -> LocalGuideSection? in
                guard let key = section["key"] as? String,
                      let title = section["title"] as? String else { return nil }
                return LocalGuideSection(
                    key: key, title: title, items: section["items"] as? [String]
                )
            }
            let rawCities = entry["cities"] as? [String: [String: Any]] ?? [:]
            let cityTips = rawCities
                .filter { cities.contains($0.key) }
                .keys.sorted()
                .compactMap { city -> LocalGuideCity? in
                    guard let tips = rawCities[city]?["tips"] as? [String] else { return nil }
                    return LocalGuideCity(city: city, tips: tips)
                }
            countries.append(
                LocalGuideCountry(
                    countryCode: code,
                    name: entry["name"] as? String ?? code,
                    sections: sections,
                    cities: cityTips
                )
            )
        }

        return LocalGuide(
            tripId: tripId,
            countries: countries,
            citiesCovered: cities,
            dataGaps: gaps
        )
    }
}
