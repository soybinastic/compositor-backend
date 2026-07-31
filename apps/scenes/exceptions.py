class SceneNotFoundError(Exception):
    pass


class ActiveSceneDeleteError(Exception):
    pass


class CountdownSceneNotActivatableError(Exception):
    pass


class CountdownAlreadyActiveError(Exception):
    pass


class InvalidCountdownTargetError(Exception):
    pass
