import Foundation

/// The proactive suggestion feed and the two human verdicts on a suggestion.
///
/// `approve` / `dismiss` are writes and are never served from cache: the Today screen has to know
/// whether the server actually recorded the human's decision.
protocol ProactiveRepository {
    func suggestions(
        tripId: String?,
        status: String?,
        limit: Int,
        cursor: String?
    ) async throws -> SuggestionFeed
    func approve(suggestionId: String) async throws -> SuggestionApprovalResponse
    func dismiss(suggestionId: String) async throws -> SuggestionDismissResponse
}

final class RemoteProactiveRepository: ProactiveRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func suggestions(
        tripId: String?,
        status: String?,
        limit: Int,
        cursor: String?
    ) async throws -> SuggestionFeed {
        try await client.send(
            Endpoints.proactiveSuggestions(
                tripId: tripId,
                status: status,
                limit: limit,
                cursor: cursor
            ),
            as: SuggestionFeed.self
        )
    }

    func approve(suggestionId: String) async throws -> SuggestionApprovalResponse {
        try await client.send(
            Endpoints.approveSuggestion(id: suggestionId),
            as: SuggestionApprovalResponse.self
        )
    }

    func dismiss(suggestionId: String) async throws -> SuggestionDismissResponse {
        try await client.send(
            Endpoints.dismissSuggestion(id: suggestionId),
            as: SuggestionDismissResponse.self
        )
    }
}

/// Read-through wrapper for the unfiltered feed only.
///
/// The cache is deliberately keyed on “no filters”: a filtered feed stored under a different query
/// would be presented as if it were the default list the next time the device is offline.
final class OfflineProactiveRepository: ProactiveRepository {
    private let remote: ProactiveRepository
    private let feedCache: JSONFileCache<SuggestionFeed>
    private let indicator: OfflineIndicator

    init(
        remote: ProactiveRepository,
        feedCache: JSONFileCache<SuggestionFeed>,
        indicator: OfflineIndicator
    ) {
        self.remote = remote
        self.feedCache = feedCache
        self.indicator = indicator
    }

    convenience init(remote: ProactiveRepository, directory: URL, indicator: OfflineIndicator) {
        self.init(
            remote: remote,
            feedCache: JSONFileCache<SuggestionFeed>(
                directory: directory,
                fileName: "suggestion-feed.json"
            ),
            indicator: indicator
        )
    }

    func suggestions(
        tripId: String?,
        status: String?,
        limit: Int,
        cursor: String?
    ) async throws -> SuggestionFeed {
        let isDefaultQuery = tripId == nil && status == nil && cursor == nil
        do {
            let feed = try await remote.suggestions(
                tripId: tripId,
                status: status,
                limit: limit,
                cursor: cursor
            )
            if isDefaultQuery {
                feedCache.save(feed)
            }
            indicator.markServedFromServer()
            return feed
        } catch {
            guard isDefaultQuery, let cached = feedCache.load() else { throw error }
            indicator.markServedFromCache("主动建议")
            return cached
        }
    }

    func approve(suggestionId: String) async throws -> SuggestionApprovalResponse {
        try await remote.approve(suggestionId: suggestionId)
    }

    func dismiss(suggestionId: String) async throws -> SuggestionDismissResponse {
        try await remote.dismiss(suggestionId: suggestionId)
    }
}