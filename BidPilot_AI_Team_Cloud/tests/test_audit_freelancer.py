"""Malformed bid acknowledgements must preserve an uncertain send result."""
import httpx
import pytest

from app.freelancer import FreelancerAPI, FreelancerError


@pytest.mark.parametrize('control', ['\x00', '\x01', '\x08', '\x0e', '\x1b', '\x7f'])
def test_control_character_token_is_rejected_before_client_creation(monkeypatch, control):
    def no_client(**kwargs):
        pytest.fail('Invalid OAuth token reached HTTP client creation')

    monkeypatch.setattr(httpx, 'Client', no_client)
    with pytest.raises(FreelancerError, match='valid Freelancer OAuth token'):
        FreelancerAPI('synthetic' + control + 'token').me()


@pytest.mark.parametrize('bid_id', [-1, True, {'id': 789}, [789], 'not-an-id', '１２３', '9' * 21])
@pytest.mark.parametrize('nested', [False, True])
def test_malformed_bid_id_is_uncertain_after_single_request(monkeypatch, bid_id, nested):
    requests = []

    def respond(request):
        requests.append(request)
        bid = {'id': bid_id}
        return httpx.Response(200, json={'status': 'success', 'result': {'bid': bid} if nested else bid})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real_client(
        transport=httpx.MockTransport(respond), **kwargs))
    with pytest.raises(FreelancerError, match='uncertain'):
        FreelancerAPI('synthetic-oauth').place_bid(456, 123, 'A detailed project proposal.', 100, 3)
    assert len(requests) == 1


@pytest.mark.parametrize('nested', [False, True])
def test_valid_decimal_bid_id_is_preserved(monkeypatch, nested):
    bid = {'id': '789'}
    real_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            'status': 'success', 'result': {'bid': bid} if nested else bid})), **kwargs))
    result = FreelancerAPI('synthetic-oauth').place_bid(456, 123, 'A detailed project proposal.', 100, 3)
    assert result['bid']['id'] == '789'
