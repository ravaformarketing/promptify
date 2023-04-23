import os
import uuid
from glob import glob
import datetime
from pathlib import Path
from prompt_cache import PromptCache
from template_loader import TemplateLoader
from conversation_logger import ConversationLogger

from typing import List, Dict, Any, Optional
from jinja2 import Environment, FileSystemLoader, meta, Template


class Prompter:

    """
    A class to generate prompts and obtain completions from a language model.
    Parameters
    ----------
    model : any
        A language model to generate text from.

    allowed_missing_variables : list of str, optional
        A list of variable names that are allowed to be missing from the template. Default is ['examples', 'description', 'output_format'].
    default_variable_values : dict of str: any, optional
        A dictionary mapping variable names to default values to be used in the template.
        If a variable is not found in the input dictionary or in the default values, it will be assumed to be required and an error will be raised. Default is an empty dictionary.
    max_completion_length : int, optional
        The maximum length of completions generated by the model. Default is 20.
    cache_prompt : bool, optional
        A flag indicating whether to cache prompt-completion pairs. Default is False.

    Methods
    -------
    get_available_templates(template_path: str) -> Dict[str, str]:
        Returns a dictionary of available templates in a directory.

    update_default_variable_values(new_defaults: Dict[str, Any]) -> None:
        Updates the default variable values with the given dictionary.

    load_multiple_templates(templates: List) -> dict:
        Loads multiple templates from a list and returns a dictionary containing their information.

    load_template(template: str) -> dict:
        Loads a single template and returns its information as a dictionary.

    verify_template_path(templates_path: str) -> None:
        Raises an error if a given template path does not exist.

    list_templates(environment) -> List[str]:
        Returns a list of available templates.

    get_template_variables(environment, template_name) -> List[str]:
        Returns a list of variables in a template.

    generate_prompt(text_input, **kwargs) -> str:
        Generates a prompt from a template and input values.

    fit(text_input, **kwargs) -> List[str]:
        Returns model outputs for a given prompt.

    """

    def __init__(
        self,
        model,
        allowed_missing_variables: Optional[List[str]] = None,
        default_variable_values: Optional[Dict[str, Any]] = None,
        max_completion_length: int = 20,
        cache_prompt: bool = False,
        cache_size: int = 200,
    ) -> None:
        """
        Initialize Prompter with default or user-specified settings.

        Parameters
        ----------
        model : any
            A language model to generate text from.
        template : str, optional
            A Jinja2 template to use for generating the prompt. Must be a valid file path.
        raw_prompt : bool, optional
            A flag indicating whether to use raw prompts or not. Default is False.
        allowed_missing_variables : list of str, optional
            A list of variable names that are allowed to be missing from the template. Default is ['examples', 'description', 'output_format'].
        default_variable_values : dict of str: any, optional
            A dictionary mapping variable names to default values to be used in the template.
            If a variable is not found in the input dictionary or in the default values, it will be assumed to be required and an error will be raised. Default is an empty dictionary.
        max_completion_length : int, optional
            The maximum length of completions generated by the model. Default is 20.
        cache_prompt : bool, optional
            A flag indicating whether to cache prompt-completion pairs. Default is False.
        cache_size : int, optional
            Cache size.
        """

        self.model = model
        self.max_completion_length = max_completion_length
        self.cache_prompt = cache_prompt
        self.prompt_cache = PromptCache(cache_size)
        self.template_loader = TemplateLoader()

        self.allowed_missing_variables = [
            "examples",
            "description",
            "output_format",
        ]
        self.allowed_missing_variables.extend(allowed_missing_variables or [])

        self.default_variable_values = default_variable_values or {}
        self.model_args_count = self.model.run.__code__.co_argcount
        self.model_variables = self.model.run.__code__.co_varnames[
            1 : self.model_args_count
        ]

        self.conversation_path = os.getcwd()
        self.model_dict = {
            key: value
            for key, value in model.__dict__.items()
            if is_string_or_digit(value)
        }
        self.logger = ConversationLogger(self.conversation_path, self.model_dict)

    def update_default_variable_values(self, new_defaults: Dict[str, Any]) -> None:
        self.default_variable_values.update(new_defaults)

    def generate_prompt(self, template, text_input, **kwargs) -> str:
        """
        Generates a prompt based on a template and input variables.

        Parameters
        ----------
        text_input : str
            The input text to use in the prompt.
        **kwargs : dict
            Additional variables to be used in the template.

        Returns
        -------
        str
            The generated prompt string.
        """

        loader = self.template_loader.load_template(
            template, self.model_dict["model"], kwargs.get("from_string", False)
        )

        kwargs["text_input"] = text_input

        if loader["environment"]:
            variables = self.template_loader.get_template_variables(
                loader["environment"], loader["template_name"]
            )
            variables_dict = {
                temp_variable_: kwargs.get(temp_variable_, None)
                for temp_variable_ in variables
            }

            variables_missing = [
                variable
                for variable in variables
                if variable not in kwargs
                and variable not in self.allowed_missing_variables
                and variable not in self.default_variable_values
            ]

            if variables_missing:
                raise ValueError(
                    f"Missing required variables in template {', '.join(variables_missing)}"
                )
        else:
            variables_dict = {"data": None}

        kwargs.update(self.default_variable_values)
        prompt = loader["template"].render(**kwargs).strip()
        return prompt, variables_dict

    def fit(self, template, text_input, **kwargs):
        """
        Generates model output for a given input using a template.

        Parameters
        ----------
        text_input : str
            The input text to use in the prompt.
        **kwargs : dict
            Additional variables to be used in the template.

        Returns
        -------
        List[str]
            A list of model output strings
        """

        prompt, variables_dict = self.generate_prompt(template, text_input, **kwargs)

        if "verbose" in kwargs:
            if kwargs["verbose"]:
                print(prompt)

        if self.cache_prompt:
            output = self.prompt_cache.get(prompt)
            if output:
                return output

        response = self.model.execute_with_retry(prompts=[prompt])
        outputs = [
            self.model.model_output(
                output, max_completion_length=self.max_completion_length
            )
            for output in response
        ]

        if self.cache_prompt:
            self.prompt_cache.add(prompt, outputs)

        message = create_message(
            template,
            prompt,
            outputs[0]["text"],
            outputs[0]["parsed"]["data"]["completion"],
            **variables_dict,
        )
        self.logger.add_message(message)
        return outputs
