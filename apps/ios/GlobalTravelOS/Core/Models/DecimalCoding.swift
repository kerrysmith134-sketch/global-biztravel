import Foundation

/// The backend serialises `Decimal` as a JSON string (`"1000.00"`), while a few endpoints send
/// numbers. Swift's default `Decimal` decoding accepts only numbers, so finance models would fail
/// to decode. These helpers accept both shapes.
extension KeyedDecodingContainer {
    func decodeDecimalIfPresent(forKey key: Key) throws -> Decimal? {
        if let string = try? decodeIfPresent(String.self, forKey: key) {
            guard let value = Decimal(string: string, locale: Locale(identifier: "en_US_POSIX")) else {
                throw DecodingError.dataCorruptedError(
                    forKey: key,
                    in: self,
                    debugDescription: "无法把 \(string) 解析为金额"
                )
            }
            return value
        }
        if let double = try? decodeIfPresent(Double.self, forKey: key) {
            return Decimal(double)
        }
        if contains(key), (try? decodeNil(forKey: key)) == false {
            throw DecodingError.dataCorruptedError(
                forKey: key,
                in: self,
                debugDescription: "金额既不是字符串也不是数字"
            )
        }
        return nil
    }

    func decodeDecimal(forKey key: Key) throws -> Decimal {
        guard let value = try decodeDecimalIfPresent(forKey: key) else {
            throw DecodingError.keyNotFound(key, .init(codingPath: codingPath, debugDescription: "缺少金额字段"))
        }
        return value
    }
}
