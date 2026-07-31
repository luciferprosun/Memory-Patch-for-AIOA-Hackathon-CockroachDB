"""Immutable exact-media-type registry for production Step 11 parsers."""

from __future__ import annotations

from types import MappingProxyType
from typing import Callable, Mapping

from .errors import UnsupportedMediaTypeError
from .models import ParserProfile, ResourceLimits
from .parsers import PARSER_FUNCTIONS, PARSER_PROFILES, ParsedContent


ParserFunction = Callable[..., ParsedContent]


class ParserRegistry:
    """Dispatch only exact declared media types; never guess from bytes."""

    def __init__(
        self,
        functions: Mapping[str, ParserFunction] | None = None,
        profiles: Mapping[str, ParserProfile] | None = None,
    ) -> None:
        selected_functions = dict(functions or PARSER_FUNCTIONS)
        selected_profiles = dict(profiles or PARSER_PROFILES)
        if set(selected_functions) != set(selected_profiles):
            raise ValueError("parser registry functions and profiles differ")
        if any(
            media_type != profile.media_type or not callable(selected_functions[media_type])
            for media_type, profile in selected_profiles.items()
        ):
            raise ValueError("parser registry contains an invalid profile binding")
        self._functions = MappingProxyType(selected_functions)
        self._profiles = MappingProxyType(selected_profiles)

    @property
    def supported_media_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))

    def profile_for(self, media_type: str) -> ParserProfile:
        profile = self._profiles.get(media_type)
        if profile is None:
            raise UnsupportedMediaTypeError(
                "no exact production parser profile exists for this media type",
                sanitized_code="UNSUPPORTED_MEDIA_TYPE",
            )
        return profile

    def parse(
        self,
        media_type: str,
        payload: bytes,
        *,
        expected_sha256: str,
        expected_length: int,
        limits: ResourceLimits,
    ) -> tuple[ParserProfile, ParsedContent]:
        profile = self.profile_for(media_type)
        result = self._functions[media_type](
            payload,
            expected_sha256=expected_sha256,
            expected_length=expected_length,
            limits=limits,
            profile=profile,
        )
        return profile, result
