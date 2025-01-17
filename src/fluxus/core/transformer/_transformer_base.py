# -----------------------------------------------------------------------------
# © 2024 Boston Consulting Group. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# -----------------------------------------------------------------------------

"""
Implementation of conduit base classes.
"""

from __future__ import annotations

import logging
from abc import ABCMeta, abstractmethod
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from typing import Any, Generic, TypeVar, final, overload

from typing_extensions import Self

from pytools.api import inheritdoc
from pytools.asyncio import async_flatten
from pytools.typing import (
    get_common_generic_base,
    get_common_generic_subclass,
    issubclass_generic,
)

from ..._passthrough import Passthrough
from .. import ConcurrentConduit, Processor, SerialProcessor, SerialSource, Source
from ..producer import BaseProducer, SerialProducer

log = logging.getLogger(__name__)

__all__ = [
    "BaseTransformer",
    "ConcurrentTransformer",
    "SerialTransformer",
]

#
# Type variables
#
# Naming convention used here:
# _ret for covariant type variables used in return positions
# _arg for contravariant type variables used in argument positions
#

T_SourceProduct_arg = TypeVar("T_SourceProduct_arg", contravariant=True)
T_Product_ret = TypeVar("T_Product_ret", covariant=True)
T_TransformedProduct_ret = TypeVar("T_TransformedProduct_ret", covariant=True)


#
# Classes
#


class BaseTransformer(
    Processor[T_SourceProduct_arg, T_TransformedProduct_ret],
    Source[T_TransformedProduct_ret],
    Generic[T_SourceProduct_arg, T_TransformedProduct_ret],
    metaclass=ABCMeta,
):
    """
    A conduit that transforms products from a source – this is either a
    :class:`.SerialTransformer` or a :class:`.ConcurrentTransformer`.
    """

    @abstractmethod
    def iter_concurrent_producers(
        self, *, source: SerialProducer[T_SourceProduct_arg]
    ) -> Iterator[SerialProducer[T_TransformedProduct_ret]]:
        """
        Generate serial producers which, run concurrently, will produce all transformed
        products.

        :param source: the source producer whose products to transform
        :return: the concurrent producers for all concurrent paths of this transformer
        """

    def __and__(
        self,
        other: (
            BaseTransformer[T_SourceProduct_arg, T_TransformedProduct_ret] | Passthrough
        ),
    ) -> BaseTransformer[T_SourceProduct_arg, T_TransformedProduct_ret]:
        input_type: type[T_SourceProduct_arg]
        product_type: type[T_TransformedProduct_ret]

        if isinstance(other, Passthrough):
            _validate_concurrent_passthrough(self)
            input_type = self.input_type
            product_type = self.product_type
        elif not isinstance(other, BaseTransformer):
            return NotImplemented
        else:
            input_type = get_common_generic_subclass(
                (self.input_type, other.input_type)
            )
            product_type = get_common_generic_base(
                (self.product_type, other.product_type)
            )
        from . import SimpleConcurrentTransformer

        return SimpleConcurrentTransformer[
            input_type, product_type  # type: ignore[valid-type]
        ](self, other)

    def __rand__(
        self, other: Passthrough
    ) -> BaseTransformer[T_SourceProduct_arg, T_TransformedProduct_ret]:
        if isinstance(other, Passthrough):
            _validate_concurrent_passthrough(self)

            from . import SimpleConcurrentTransformer

            return SimpleConcurrentTransformer[
                self.input_type, self.product_type  # type: ignore[name-defined]
            ](other, self)
        else:
            return NotImplemented

    @overload
    def __rshift__(
        self,
        other: SerialTransformer[T_TransformedProduct_ret, T_Product_ret],
    ) -> (
        BaseTransformer[T_SourceProduct_arg, T_Product_ret]
        | SerialTransformer[T_SourceProduct_arg, T_Product_ret]
    ):
        pass  # pragma: no cover

    @overload
    def __rshift__(
        self,
        other: BaseTransformer[T_TransformedProduct_ret, T_Product_ret],
    ) -> BaseTransformer[T_SourceProduct_arg, T_Product_ret]:
        pass  # pragma: no cover

    def __rshift__(
        self,
        other: (
            BaseTransformer[T_TransformedProduct_ret, T_Product_ret]
            | SerialTransformer[T_TransformedProduct_ret, T_Product_ret]
        ),
    ) -> (
        BaseTransformer[T_SourceProduct_arg, T_Product_ret]
        | SerialTransformer[T_SourceProduct_arg, T_Product_ret]
    ):
        if isinstance(other, BaseTransformer):
            from ._chained_ import _ChainedConcurrentTransformer

            return _ChainedConcurrentTransformer(self, other)
        else:
            return NotImplemented

    @overload
    def __rrshift__(
        self, other: SerialProducer[T_SourceProduct_arg]
    ) -> (
        SerialProducer[T_TransformedProduct_ret]
        | BaseProducer[T_TransformedProduct_ret]
    ):
        pass

    @overload
    def __rrshift__(
        self, other: BaseProducer[T_SourceProduct_arg]
    ) -> BaseProducer[T_TransformedProduct_ret]:
        pass

    def __rrshift__(
        self,
        other: BaseProducer[T_SourceProduct_arg],
    ) -> BaseProducer[T_TransformedProduct_ret] | Self:
        if isinstance(other, SerialProducer):
            from ._chained_ import _ChainedConcurrentTransformedProducer

            # noinspection PyTypeChecker
            return _ChainedConcurrentTransformedProducer(source=other, transformer=self)
        elif isinstance(other, BaseProducer):
            from ._chained_ import _ChainedConcurrentProducer

            return _ChainedConcurrentProducer(source=other, transformer=self)
        else:
            return NotImplemented


@inheritdoc(match="[see superclass]")
class SerialTransformer(
    SerialProcessor[T_SourceProduct_arg, T_TransformedProduct_ret],
    SerialSource[T_TransformedProduct_ret],
    BaseTransformer[T_SourceProduct_arg, T_TransformedProduct_ret],
    Generic[T_SourceProduct_arg, T_TransformedProduct_ret],
    metaclass=ABCMeta,
):
    """
    A transformer that generates new products from the products of a producer.
    """

    @final
    def iter_concurrent_producers(
        self, *, source: SerialProducer[T_SourceProduct_arg]
    ) -> Iterator[SerialProducer[T_TransformedProduct_ret]]:
        """[see superclass]"""
        yield source >> self

    def process(
        self, input: Iterable[T_SourceProduct_arg]
    ) -> Iterator[T_TransformedProduct_ret]:
        """[see superclass]"""
        for product in input:
            yield from self.transform(product)

    def aprocess(
        self, input: AsyncIterable[T_SourceProduct_arg]
    ) -> AsyncIterator[T_TransformedProduct_ret]:
        """[see superclass]"""
        # noinspection PyTypeChecker
        return async_flatten(self.atransform(product) async for product in input)

    @abstractmethod
    def transform(
        self, source_product: T_SourceProduct_arg
    ) -> Iterator[T_TransformedProduct_ret]:
        """
        Generate a new product from an existing product.

        :param source_product: an existing product to use as input
        :return: the new product
        """

    async def atransform(
        self, source_product: T_SourceProduct_arg
    ) -> AsyncIterator[T_TransformedProduct_ret]:
        """
        Generate a new product asynchronously, using an existing product as input.

        By default, defers to the synchronous variant, :meth:`transform`.

        :param source_product: the existing product to use as input
        :return: the new product
        """
        for tx in self.transform(source_product):
            yield tx

    @overload
    def __rshift__(
        self,
        other: SerialTransformer[T_TransformedProduct_ret, T_Product_ret],
    ) -> SerialTransformer[T_SourceProduct_arg, T_Product_ret]:
        pass  # pragma: no cover

    @overload
    def __rshift__(
        self,
        other: BaseTransformer[T_TransformedProduct_ret, T_Product_ret],
    ) -> BaseTransformer[T_SourceProduct_arg, T_Product_ret]:
        pass  # pragma: no cover

    def __rshift__(
        self,
        other: (
            BaseTransformer[T_TransformedProduct_ret, T_Product_ret]
            | SerialTransformer[T_TransformedProduct_ret, T_Product_ret]
        ),
    ) -> (
        BaseTransformer[T_SourceProduct_arg, T_Product_ret]
        | SerialTransformer[T_SourceProduct_arg, T_Product_ret]
    ):
        # Create a combined transformer where the output of this transformer is used as
        # the input of the other transformer
        if isinstance(other, SerialTransformer):
            # We import locally to avoid circular imports
            from ._chained_ import _ChainedTransformer

            return _ChainedTransformer(self, other)

        return super().__rshift__(other)

    @overload
    def __rrshift__(
        self, other: SerialProducer[T_SourceProduct_arg]
    ) -> SerialProducer[T_TransformedProduct_ret]:
        pass  # pragma: no cover

    @overload
    def __rrshift__(
        self, other: BaseProducer[T_SourceProduct_arg]
    ) -> BaseProducer[T_TransformedProduct_ret]:
        pass  # pragma: no cover

    def __rrshift__(
        self, other: BaseProducer[T_SourceProduct_arg]
    ) -> BaseProducer[T_TransformedProduct_ret]:
        if isinstance(other, SerialProducer):
            # We import locally to avoid circular imports
            from ._chained_ import _ChainedProducer

            return _ChainedProducer(producer=other, transformer=self)
        else:
            return super().__rrshift__(other)


#
# Auxiliary functions
#


def _validate_concurrent_passthrough(
    conduit: BaseTransformer[Any, Any] | Passthrough
) -> None:
    """
    Validate that the given conduit is valid as a concurrent conduit with a passthrough.

    To be valid, its input type must be a subtype of its product type.

    :param conduit: the conduit to validate
    """

    if not (
        isinstance(conduit, Passthrough)
        or (issubclass_generic(conduit.input_type, conduit.product_type))
    ):
        raise TypeError(
            "Conduit is not a valid concurrent conduit with a passthrough because its "
            f"input type {conduit.input_type} is not a subtype of its product type "
            f"{conduit.product_type}:\n{conduit}"
        )


class ConcurrentTransformer(
    BaseTransformer[T_SourceProduct_arg, T_TransformedProduct_ret],
    ConcurrentConduit[T_TransformedProduct_ret],
    Generic[T_SourceProduct_arg, T_TransformedProduct_ret],
    metaclass=ABCMeta,
):
    """
    A collection of one or more transformers, operating in parallel.
    """

    def process(
        self, input: Iterable[T_SourceProduct_arg]
    ) -> Iterator[T_TransformedProduct_ret]:
        """
        Transform the given products.

        :param input: the products to transform
        :return: the transformed products
        """
        from ...simple import SimpleProducer

        return iter(
            SimpleProducer[self.input_type](input) >> self  # type: ignore[name-defined]
        )

    def aprocess(
        self, input: AsyncIterable[T_SourceProduct_arg]
    ) -> AsyncIterator[T_TransformedProduct_ret]:
        """
        Transform the given products asynchronously.

        :param input: the products to transform
        :return: the transformed products
        """
        from ...simple import SimpleAsyncProducer

        return aiter(
            SimpleAsyncProducer[self.input_type](input)  # type: ignore[name-defined]
            >> self
        )
