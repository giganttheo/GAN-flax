import jax
import jax.numpy as jnp
import flax
from flax import linen as nn
from utils import sample_latent, fetch_oh_labels, plot_conditional
from architecture.dcgan import Generator, Discriminator
from models.base_model import Model

#Losses

def bce_logits(logit, label):
  """
  Implements the BCE with logits loss, as described:
  https://github.com/pytorch/pytorch/issues/751
  """
  neg_abs = -jnp.abs(logit)
  batch_bce = jnp.maximum(logit, 0) - logit * label + jnp.log(1 + jnp.exp(neg_abs))
  return jnp.mean(batch_bce)


def loss_generator(params_g, params_d, vars_g, vars_d, data, key):
  oh_labels, oh_labels_img = fetch_oh_labels(data[1])

  latent = sample_latent(key, shape=(data[0].shape[0], 64))
  latent = jnp.concatenate([latent, oh_labels], axis=-1)

  fake_data, vars_g = Generator().apply(
      {'params': params_g, 'batch_stats': vars_g['batch_stats']},
      latent, mutable=['batch_stats']
  )

  fake_data = jnp.concatenate([fake_data, oh_labels_img], axis=-1)

  fake_preds, vars_d = Discriminator().apply(
      {'params': params_d, 'batch_stats': vars_d['batch_stats']},
      fake_data, mutable=['batch_stats']
  )

  loss = -jnp.mean(jnp.log(nn.sigmoid(fake_preds)))
  return loss, (vars_g, vars_d)


def loss_discriminator(params_d, params_g, vars_g, vars_d, data, key):
  oh_labels, oh_labels_img = fetch_oh_labels(data[1])

  latent = sample_latent(key, shape=(data[0].shape[0], 64))
  latent = jnp.concatenate([latent, oh_labels], axis=-1)

  fake_data, vars_g = Generator().apply(
      {'params': params_g, 'batch_stats': vars_g['batch_stats']},
      latent, mutable=['batch_stats']
  )

  fake_data = jnp.concatenate([fake_data, oh_labels_img], axis=-1)
  real_data = jnp.concatenate([data[0], oh_labels_img], axis=-1)

  fake_preds, vars_d = Discriminator().apply(
      {'params': params_d, 'batch_stats': vars_d['batch_stats']},
      fake_data, mutable=['batch_stats']
  )

  real_preds, vars_d = Discriminator().apply(
      {'params': params_d, 'batch_stats': vars_d['batch_stats']},
      real_data, mutable=['batch_stats']
  )

  real_loss = bce_logits(real_preds, jnp.ones((data[0].shape[0],), dtype=jnp.int32))
  fake_loss = bce_logits(fake_preds, jnp.zeros((data[0].shape[0],), dtype=jnp.int32))
  loss = (real_loss + fake_loss) / 2

  return loss, (vars_g, vars_d)


#Train and eval functions

@jax.jit
def train_step(data, vars_g, vars_d, optim_g, optim_d, rng):
  key, key_gen, key_disc = jax.random.split(rng, 3)

  # Train the generator
  grad_fn_generator = jax.value_and_grad(loss_generator, has_aux=True)
  (loss_g, (vars_g, vars_d)), grad_g = grad_fn_generator(
      optim_g.target, optim_d.target, vars_g, vars_d, data, key_gen
  )

  optim_g = optim_g.apply_gradient(grad_g)
  
  # Train the discriminator
  grad_fn_discriminator = jax.value_and_grad(loss_discriminator, has_aux=True)
  (loss_d, (vars_g, vars_d)), grad_d = grad_fn_discriminator(
      optim_d.target, optim_g.target, vars_g, vars_d, data, key_disc
  )

  optim_d = optim_d.apply_gradient(grad_d)

  loss = {'generator': loss_g, 'discriminator': loss_d}
  return loss, vars_g, vars_d, optim_g, optim_d, key


@jax.jit
def eval_step(params, vars, latent):  
  fake_data, _ = Generator(training=False).apply(
      {'params': params, 'batch_stats': vars['batch_stats']},
      latent, mutable=['batch_stats']
  )

  return fake_data


#Training loop

class ConditionalGan(Model):
    def __init__(self):
        pass

    def train(self, data_gen, batches_in_epoch, key, verbose=1):
            epochs = 101
            key, key_gen, key_disc, key_latent = jax.random.split(key, 4)

            # Retrieve shapes for generator and discriminator input.
            latent = sample_latent(key_latent, shape=(100, 64))
            labels = jnp.tile(jnp.arange(10), 10)
            oh_labels = jax.nn.one_hot(labels, 10)
            latent = jnp.concatenate([latent, oh_labels], axis=-1)

            image_shape = next(data_gen)[0].shape[:3] + (11,)

            # Generate initial variables (parameters and batch statistics).
            vars_g = Generator().init(key_gen, jnp.ones(latent.shape, jnp.float32))
            vars_d = Discriminator().init(key_disc, jnp.ones(image_shape, jnp.float32))

            # Create optimizers.
            optim_g = flax.optim.Adam(0.0001, 0.5, 0.999).create(vars_g['params'])
            optim_d = flax.optim.Adam(0.0002, 0.5, 0.999).create(vars_d['params'])

            loss = {'generator': [], 'discriminator': []}

            for epoch in range(1, epochs):
                for batch in range(batches_in_epoch):
                    data = next(data_gen)

                    batch_loss, vars_g, vars_d, optim_g, optim_d, key = train_step(
                        data, vars_g, vars_d, optim_g, optim_d, key
                    )

                    loss['generator'].append(batch_loss['generator'])
                    loss['discriminator'].append(batch_loss['discriminator'])
                
                sample = eval_step(optim_g.target, vars_g, latent)
                plot_conditional(sample, loss, labels, epoch)

