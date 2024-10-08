from sqlalchemy import String, Float, DateTime, func, Integer, ForeignKey, Text, BigInteger
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    created: Mapped[DateTime] = mapped_column(DateTime, default=func.now())
    updated: Mapped[DateTime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class AdminExercises(Base):
    __tablename__ = 'admin_exercises'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(), unique=True)
    image: Mapped[str] = mapped_column(String())
    description: Mapped[str] = mapped_column(Text)


class Banner(Base):
    __tablename__ = 'banner'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(15), unique=True)
    image: Mapped[str] = mapped_column(String(150), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)


class User(Base):
    __tablename__ = 'user'

    id: Mapped[int] = mapped_column(Integer(), primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(20), nullable=False)
    weight: Mapped[float] = mapped_column(Float(), nullable=False)


class TrainingProgram(Base):
    __tablename__ = 'training_program'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user.user_id'), nullable=False)

    # Relationships
    user: Mapped['User'] = relationship(backref='training_program')


class TrainingDay(Base):
    __tablename__ = 'training_day'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    day_of_week: Mapped[str] = mapped_column(String(20), nullable=False)  # Например, 'Monday', 'Tuesday'
    training_program_id: Mapped[int] = mapped_column(ForeignKey('training_program.id'), nullable=False)

    # Relationships
    training_program: Mapped['TrainingProgram'] = relationship(backref='training_day')


class Exercise(Base):
    __tablename__ = 'exercise'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    description: Mapped[str] = mapped_column(Text)
    image: Mapped[str] = mapped_column(String(150))
    training_day_id: Mapped[int] = mapped_column(ForeignKey("training_day.id"), nullable=False)

    # Relationships
    training_day: Mapped['TrainingDay'] = relationship(backref='exercise')


class Set(Base):
    __tablename__ = 'set'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey('exercise.id'), nullable=False)
    weight: Mapped[float] = mapped_column(Float(), nullable=False)
    repetitions: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    exercise: Mapped['Exercise'] = relationship(backref='set')
