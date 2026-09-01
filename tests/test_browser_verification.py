from scripts.browser_verification import wait_for_user_verification


class FakeLocator:
    def __init__(self, page):
        self.page = page

    def inner_text(self, timeout=0):
        del timeout
        return self.page.states[self.page.index][2]


class FakePage:
    def __init__(self, states):
        self.states = states
        self.index = 0
        self.waits = []

    @property
    def url(self):
        return self.states[self.index][0]

    def title(self):
        return self.states[self.index][1]

    def locator(self, selector):
        assert selector == "body"
        return FakeLocator(self)

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        if self.index + 1 < len(self.states):
            self.index += 1


def test_body_only_challenge_waits_until_user_finishes():
    page = FakePage(
        [
            ("https://www.douyin.com/note/1", "抖音", "请完成安全验证，拖动滑块"),
            ("https://www.douyin.com/note/1", "作品标题", "作者正文"),
        ]
    )

    assert wait_for_user_verification(page, timeout_seconds=10, poll_seconds=5) is True
    assert page.waits == [5000]


def test_body_only_login_waits_until_user_finishes():
    page = FakePage(
        [
            ("https://www.douyin.com/note/1", "抖音", "请先登录后查看"),
            ("https://www.douyin.com/note/1", "作品标题", "作者正文"),
        ]
    )

    assert wait_for_user_verification(page, timeout_seconds=10, poll_seconds=5) is True


def test_verification_timeout_is_reported_to_caller():
    page = FakePage(
        [("https://www.douyin.com/note/1", "抖音", "安全验证 captcha")]
    )

    assert wait_for_user_verification(page, timeout_seconds=10, poll_seconds=5) is False
    assert page.waits == [5000, 5000]


def test_normal_content_does_not_wait():
    page = FakePage(
        [("https://www.douyin.com/note/1", "作品标题", "正常作者正文")]
    )

    assert wait_for_user_verification(page, timeout_seconds=10, poll_seconds=5) is True
    assert page.waits == []
