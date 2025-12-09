from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from src.services.tracker import CryptoTracker
from src.models.coin import TrackedCoin, CoinPrice


# Mock data from CoinGecko API
MOCK_COINS_LIST = [
    {'id': 'bitcoin', 'symbol': 'btc', 'name': 'Bitcoin'},
    {'id': 'ethereum', 'symbol': 'eth', 'name': 'Ethereum'},
    {'id': 'ripple', 'symbol': 'xrp', 'name': 'XRP'},
    {'id': 'spam-coin-peg', 'symbol': 'spam.x', 'name': 'SpamCoin'},
]

@pytest.fixture
def mock_crypto_client():
    """Fixture for a mocked BaseCryptoClient."""
    client = MagicMock()
    client.get_supported_coins_with_details.return_value = MOCK_COINS_LIST
    return client

@pytest.fixture
def mock_db_connection():
    """Fixture to mock the database connection."""
    conn = MagicMock()
    return conn

@pytest.fixture
def tracker_service(mock_crypto_client, mock_db_connection):
    """Fixture for a CryptoTracker service instance with a mocked client and DB."""
    # We disable the real DB connection for unit tests
    with patch('src.services.tracker.get_default_connection', return_value=mock_db_connection):
        service = CryptoTracker(client=mock_crypto_client)
        # Prevent the real connect() from being called in tests
        service.connection.connect = MagicMock()
        return service


def test_search_filters_spam_tokens(tracker_service: CryptoTracker):
    """
    Verify that the search function filters out tokens with weird symbols.
    """
    # Act
    results = tracker_service.search_coins(query="spam")

    # Assert
    assert len(results) == 0

def test_search_finds_valid_tokens(tracker_service: CryptoTracker):
    """
    Verify that search correctly finds coins by name, symbol, or id.
    """
    # Act & Assert
    assert len(tracker_service.search_coins(query="btc")) == 1
    assert tracker_service.search_coins(query="btc")[0]['id'] == 'bitcoin'
    
    assert len(tracker_service.search_coins(query="ethereum")) == 1
    assert tracker_service.search_coins(query="ethereum")[0]['id'] == 'ethereum'

    assert len(tracker_service.search_coins(query="xrp")) >= 1
    assert tracker_service.search_coins(query="xrp")[0]['id'] == 'ripple'


@patch('src.services.tracker.CryptoTracker.search_coins')
@patch('builtins.input', side_effect=['1', '']) # User chooses '1'
@patch('src.services.tracker.TrackedCoinDocument')
def test_add_coin_interactive_success(mock_doc, mock_input, mock_search, tracker_service: CryptoTracker):
    """
    Test the interactive add coin workflow succeeds with valid input.
    """
    # Arrange
    # Make search_coins return 2 results to ensure the interactive prompt is shown
    mock_search.return_value = [
        {'id': 'bitcoin', 'symbol': 'btc', 'name': 'Bitcoin'},
        {'id': 'ethereum', 'symbol': 'eth', 'name': 'Ethereum'}
    ]
    # The to_dataclass method should be mocked on the document instance
    mock_doc.return_value.to_dataclass.return_value = TrackedCoin(
        coin_id='bitcoin', symbol='btc', name='Bitcoin', is_active=True
    )
    mock_doc.objects.return_value.first.return_value = None # No existing coin
    mock_doc.return_value.save.return_value = mock_doc.return_value # save returns self

    # Act
    result = tracker_service.add_tracked_coin_interactive(query="any query")

    # Assert
    assert result is not None
    assert result.coin_id == 'bitcoin'
    mock_input.assert_called_with("Select number to track (1-2, or 0 to cancel): ")


@patch('src.services.tracker.CryptoTracker.search_coins')
@patch('builtins.input', side_effect=['99', '0']) # User enters out-of-range, then cancels
@patch('src.services.tracker.TrackedCoinDocument')
def test_add_coin_interactive_out_of_range_and_cancel(mock_doc, mock_input, mock_search, tracker_service: CryptoTracker):
    """
    Test that the interactive add function handles out-of-range and cancel inputs.
    """
    # Arrange
    # Make search_coins return 2 results to ensure the interactive prompt is shown
    mock_search.return_value = [
        {'id': 'c1', 'symbol': 'c1', 'name': 'Coin 1'},
        {'id': 'c2', 'symbol': 'c2', 'name': 'Coin 2'}
    ]
    # Ensure the coin is not considered 'already tracked'
    mock_doc.objects.return_value.first.return_value = None

    # Act & Assert
    with pytest.raises(ValueError, match="Operation cancelled by user"):
        tracker_service.add_tracked_coin_interactive(query="any query") # Query doesn't matter now
    
    # Ensures the loop for input validation ran before cancellation
    assert mock_input.call_count > 1



def test_record_prices_fail_safe(tracker_service: CryptoTracker):
    """
    Test that the price recording loop continues even if one coin fails.
    """
    # Arrange
    # Simulate two active coins
    active_coins = [
        TrackedCoin(coin_id='bitcoin', symbol='btc', name='Bitcoin'),
        TrackedCoin(coin_id='ethereum', symbol='eth', name='Ethereum'),
    ]
    tracker_service.list_tracked_coins = MagicMock(return_value=active_coins)
    
    # Mock record_price_for_coin to simulate one success and one failure
    tracker_service.record_price_for_coin = MagicMock(side_effect=[
        CoinPrice(coin_id='bitcoin', price=65000.0, timestamp=MagicMock()),
        RuntimeError("API failed for ethereum")
    ])

    # Act
    results = tracker_service.record_prices_for_all_tracked()

    # Assert
    # 1. Check that only one price was successfully recorded
    assert len(results) == 1
    assert results[0].coin_id == 'bitcoin'
    assert results[0].price == 65000.0
    
    # 2. Check that record_price_for_coin was called for both coins
    assert tracker_service.record_price_for_coin.call_count == 2
