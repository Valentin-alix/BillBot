from typing import ClassVar, TypeVar

T = TypeVar("T")


class SingletonTypeError(TypeError):
    pass


class Singleton(type):
    _instances: ClassVar[dict[type[object], object]] = {}

    def __call__(cls: type[T], *args: object, **kwargs: object) -> T:
        instance = Singleton._instances.get(cls)
        if instance is None:
            instance = super().__call__(*args, **kwargs)
            Singleton._instances[cls] = instance
        if not isinstance(instance, cls):
            raise SingletonTypeError
        return instance
